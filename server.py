from __future__ import annotations

import argparse
import hashlib
import json
import socket
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

import ai_coloring
from board_bridge import BoardBridge, BoardError, color_table


ROOT = Path(__file__).resolve().parent
SESSIONS: dict[str, dict] = {}
MAX_SESSIONS = 3
AI_JOBS: dict[str, dict] = {}
AI_JOBS_LOCK = threading.Lock()
MAX_AI_JOBS = 128
AI_JOB_MAX_AGE = 24 * 60 * 60


def ai_job_snapshot(job: dict) -> dict:
    return {key: job.get(key) for key in ("job_id", "kind", "status", "result", "error")}


def get_ai_job(job_id: str) -> dict | None:
    with AI_JOBS_LOCK:
        job = AI_JOBS.get(job_id)
        return ai_job_snapshot(job) if job else None


def find_existing_ai_job(job_id: str, fingerprint: str) -> dict | None:
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
        raise ai_coloring.ColoringError("判讀工作編號無效，請重新操作。", 400, "invalid_job_id")
    with AI_JOBS_LOCK:
        job = AI_JOBS.get(job_id)
        if not job:
            return None
        if job["fingerprint"] != fingerprint:
            raise ai_coloring.ColoringError("工作編號已用於不同內容，請重新操作。", 409, "job_id_conflict")
        return ai_job_snapshot(job)


def enqueue_ai_job(job_id: str, fingerprint: str, kind: str, run) -> dict:
    existing = find_existing_ai_job(job_id, fingerprint)
    if existing:
        return existing

    now = time.time()
    with AI_JOBS_LOCK:
        existing = AI_JOBS.get(job_id)
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise ai_coloring.ColoringError("工作編號已用於不同內容，請重新操作。", 409, "job_id_conflict")
            return ai_job_snapshot(existing)
        for old_id, old_job in list(AI_JOBS.items()):
            if old_job["status"] in ("completed", "failed") and now - old_job["created"] > AI_JOB_MAX_AGE:
                del AI_JOBS[old_id]
        if any(job["status"] in ("queued", "running") for job in AI_JOBS.values()):
            raise ai_coloring.ColoringError("目前已有一筆 AI 工作正在執行；完成或查詢該工作後再開始下一筆。",
                                            409, "ai_job_in_progress")
        if len(AI_JOBS) >= MAX_AI_JOBS:
            raise ai_coloring.ColoringError("本機工作記錄已滿，請重新啟動上色台後再試。", 503, "ai_job_store_full")
        job = {"job_id": job_id, "kind": kind, "status": "queued", "result": None,
               "error": None, "fingerprint": fingerprint, "created": now}
        AI_JOBS[job_id] = job

    def execute() -> None:
        with AI_JOBS_LOCK:
            current = AI_JOBS.get(job_id)
            if current:
                current["status"] = "running"
        try:
            result = run()
        except ai_coloring.ColoringError as error:
            outcome = ("failed", None, {"error": str(error), "code": error.code,
                                         "upstream_status": error.upstream_status})
        except Exception as error:
            print(f"[AI job {job_id[:8]}] local failure: {type(error).__name__}")
            outcome = ("failed", None, {"error": "本機處理時發生未預期錯誤；雲端請求不會自動重送。請查看本機終端機。",
                                         "code": "local_job_error", "upstream_status": None})
        else:
            outcome = ("completed", result, None)
        with AI_JOBS_LOCK:
            current = AI_JOBS.get(job_id)
            if current:
                current["status"], current["result"], current["error"] = outcome

    thread = threading.Thread(target=execute, daemon=True, name=f"ai-{job_id[:8]}")
    try:
        thread.start()
    except RuntimeError:
        with AI_JOBS_LOCK:
            job["status"] = "failed"
            job["error"] = {"error": "本機無法啟動 AI 工作；請稍後再試。", "code": "job_start_failed",
                            "upstream_status": None}
    return ai_job_snapshot(job)


def segment(session: dict, threshold: int, close_size: int, strokes: list[dict]) -> dict:
    gray = session["gray"]
    line_mask = np.where(gray < threshold, 255, 0).astype(np.uint8)

    for stroke in strokes:
        points = np.asarray(stroke.get("points", []), dtype=np.int32)
        if len(points) >= 2:
            width = max(1, int(stroke.get("width", 12)))
            cv2.polylines(line_mask, [points], False, 255, width, cv2.LINE_AA)

    if close_size > 1:
        size = close_size if close_size % 2 else close_size + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        line_mask = cv2.morphologyEx(line_mask, cv2.MORPH_CLOSE, kernel)

    free_space = cv2.bitwise_not(line_mask)
    count, component_map, stats, centroids = cv2.connectedComponentsWithStats(
        free_space, connectivity=8
    )

    height, width = gray.shape
    border_ids = set(np.unique(np.concatenate((
        component_map[0, :], component_map[-1, :],
        component_map[:, 0], component_map[:, -1],
    ))).tolist())

    component_ids = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 24]
    component_ids.sort(key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    kept = component_ids[:254]
    remap = np.zeros(count, dtype=np.uint8)
    regions = []

    for new_id, old_id in enumerate(kept, start=1):
        remap[old_id] = new_id
        x = int(stats[old_id, cv2.CC_STAT_LEFT])
        y = int(stats[old_id, cv2.CC_STAT_TOP])
        w = int(stats[old_id, cv2.CC_STAT_WIDTH])
        h = int(stats[old_id, cv2.CC_STAT_HEIGHT])
        region_crop = component_map[y:y + h, x:x + w]
        region_mask = (region_crop == old_id).astype(np.uint8)
        distance = cv2.distanceTransform(region_mask, cv2.DIST_L2, 5)
        _, _, _, max_location = cv2.minMaxLoc(distance)
        regions.append({
            "id": new_id,
            "area": int(stats[old_id, cv2.CC_STAT_AREA]),
            "center": [x + int(max_location[0]), y + int(max_location[1])],
            "bbox": [x, y, w, h],
            "touches_border": old_id in border_ids,
        })

    labels = remap[component_map]
    session.update({
        "labels": labels,
        "regions": regions,
        "revision": uuid.uuid4().hex,
        "threshold": threshold,
        "close_size": close_size,
        "strokes": strokes,
        "component_count": len(component_ids),
        "dropped_count": max(0, len(component_ids) - len(kept)),
    })
    return session


class WorkbenchHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server_version = "LineColorLab/0.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(self, payload: bytes, content_type: str, status: int = 200, extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, value: dict, status: int = 200) -> None:
        self.send_bytes(json.dumps(value).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self.send_bytes((ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/app.js":
            return self.send_bytes((ROOT / "app.js").read_bytes(), "text/javascript; charset=utf-8")
        if path == "/styles.css":
            return self.send_bytes((ROOT / "styles.css").read_bytes(), "text/css; charset=utf-8")
        if path == "/zoom.css":
            return self.send_bytes((ROOT / "zoom.css").read_bytes(), "text/css; charset=utf-8")
        if path == "/ai.css":
            return self.send_bytes((ROOT / "ai.css").read_bytes(), "text/css; charset=utf-8")
        if path == "/api/auto-color/config":
            if not self.local_ai_request():
                return self.send_json({"error": "API 設定只接受本機網頁的請求。"}, 403)
            return self.send_json(self.server.ai_connection.public())
        if path.startswith("/api/auto-color/jobs/"):
            if not self.local_ai_request():
                return self.send_json({"error": "AI 工作狀態只接受本機網頁的請求。"}, 403)
            job = get_ai_job(path.rsplit("/", 1)[-1])
            if not job:
                return self.send_json({"error": "找不到這筆 AI 工作；本機伺服器可能已重新啟動。",
                                       "code": "job_not_found"}, 404)
            return self.send_json(job)
        if path == "/api/compositor/config":
            board = self.server.board_renderer
            return self.send_json(board.config() if board else {"enabled": False})

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "session"]:
            session = SESSIONS.get(parts[2])
            if not session:
                return self.send_json({"error": "分區工作階段已失效，請重新載入圖片。"}, 404)
            if parts[3] == "source":
                return self.send_bytes(session["source"], session["content_type"])
            if parts[3] == "labels":
                ok, encoded = cv2.imencode(".png", session["labels"])
                if not ok:
                    return self.send_json({"error": "無法產生分區遮罩。"}, 500)
                return self.send_bytes(encoded.tobytes(), "image/png")
            if parts[3] == "raster":
                session = session.copy()
                if parse_qs(parsed.query).get("revision", [None])[0] != session["revision"]:
                    return self.send_json({"error": "分區已更新，請重新讀取。"}, 409)
                # OpenCV's BGR order encodes the label in R and the shared grayscale in G.
                gray = session["gray"]
                raster = cv2.merge((np.zeros_like(gray), gray, session["labels"]))
                ok, encoded = cv2.imencode(".png", raster, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if not ok:
                    return self.send_json({"error": "無法產生分區圖。"}, 500)
                return self.send_bytes(encoded.tobytes(), "image/png")
            if parts[3] == "regions":
                gray = session["gray"]
                return self.send_json({
                    "id": parts[2],
                    "revision": session["revision"],
                    "width": int(gray.shape[1]),
                    "height": int(gray.shape[0]),
                    "threshold": session["threshold"],
                    "close_size": session["close_size"],
                    "regions": session["regions"],
                    "component_count": session["component_count"],
                    "dropped_count": session["dropped_count"],
                })

        return self.send_json({"error": "找不到這個路徑。"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/auto-color":
            return self.auto_color()
        if parsed.path == "/api/auto-color/test":
            return self.test_ai()
        if parsed.path == "/api/auto-color/config":
            return self.update_ai_config()
        if parsed.path == "/api/auto-color/models":
            return self.get_ai_models()
        if parsed.path == "/api/compositor/render":
            return self.render_on_board()
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        if parsed.path == "/api/segment":
            image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                return self.send_json({"error": "請選取有效的 PNG 或 JPEG 圖片。"}, 400)
            height, width = image.shape[:2]
            if width * height > 30_000_000:
                return self.send_json({"error": "圖片超過 3000 萬像素，請先縮小後再試。"}, 413)

            session_id = uuid.uuid4().hex[:12]
            session = {
                "source": body,
                "content_type": self.headers.get("Content-Type", "image/jpeg"),
                "image": image,
                "gray": cv2.cvtColor(image, cv2.COLOR_BGR2GRAY),
                "created": time.time(),
            }
            threshold = max(80, min(250, int(query.get("threshold", ["205"])[0])))
            close_size = max(0, min(41, int(query.get("close", ["5"])[0])))
            segment(session, threshold, close_size, [])
            SESSIONS[session_id] = session
            while len(SESSIONS) > MAX_SESSIONS:
                oldest = min(SESSIONS, key=lambda key: SESSIONS[key]["created"])
                del SESSIONS[oldest]
            return self.send_json({"id": session_id, "width": width, "height": height})

        if parsed.path == "/api/resegment":
            try:
                request = json.loads(body.decode("utf-8"))
                session_id = str(request["id"])
                session = SESSIONS.get(session_id)
                if not session:
                    return self.send_json({"error": "分區工作階段已失效，請重新載入圖片。"}, 404)
                threshold = max(80, min(250, int(request.get("threshold", 205))))
                close_size = max(0, min(41, int(request.get("close_size", 5))))
                strokes = request.get("strokes", [])[-80:]
                segment(session, threshold, close_size, strokes)
                return self.send_json({"ok": True})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                return self.send_json({"error": f"分區設定無效：{error}"}, 400)

        return self.send_json({"error": "找不到這個路徑。"}, 404)

    def render_on_board(self) -> None:
        board = self.server.board_renderer
        if not board:
            return self.send_json({"error": "板上合成未啟用。"}, 503)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 8192:
                raise ValueError("Invalid request")
            request = json.loads(self.rfile.read(length))
            session_id = request["session_id"]
            session = SESSIONS.get(session_id, {}).copy()
            if not session:
                return self.send_json({"error": "分區工作階段已失效。"}, 404)
            if session["revision"] != request["revision"]:
                return self.send_json({"error": "分區已更新。"}, 409)
            table = color_table(session, request["colors"], request["transparent"])
            png, headers = board.render(session, table)
        except (ValueError, TypeError, KeyError):
            return self.send_json({"error": "區號或顏色資料有誤。"}, 400)
        except BoardError as error:
            return self.send_json({"error": str(error)}, 503)
        if SESSIONS.get(session_id, {}).get("revision") != session["revision"]:
            return self.send_json({"error": "分區已更新。"}, 409)
        try:
            return self.send_bytes(png, "image/png", extra=headers)
        except (BrokenPipeError, ConnectionResetError):
            # A new color, view, or revision may have cancelled this preview.
            return

    def local_ai_request(self) -> bool:
        # Settings, credentials, and billed calls are restricted to local clients.
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return (self.client_address[0] == "127.0.0.1" and
                host.split(":", 1)[0].lower() in ("127.0.0.1", "localhost") and
                origin in (None, f"http://{host}"))

    def update_ai_config(self) -> None:
        if not self.local_ai_request():
            return self.send_json({"error": "API 設定只接受本機網頁的請求。"}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "API 設定格式無效。"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError
            request = json.loads(self.rfile.read(length))
            result = self.server.ai_connection.update(request)
        except ai_coloring.ColoringError as error:
            return self.send_json({"error": str(error)}, error.status)
        except (ValueError, TypeError):
            return self.send_json({"error": "API 設定格式無效。"}, 400)
        return self.send_json(result)

    def get_ai_models(self) -> None:
        if not self.local_ai_request():
            return self.send_json({"error": "模型清單只接受本機網頁的請求。"}, 403)
        try:
            models = ai_coloring.list_models(self.server.ai_connection.snapshot())
        except ai_coloring.ColoringError as error:
            return self.send_json({"error": str(error)}, error.status)
        return self.send_json({"models": models})

    def auto_color(self) -> None:
        if not self.local_ai_request():
            return self.send_json({"error": "AI 上色只接受本機網頁的請求。"}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "參考圖請求格式無效。"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= ai_coloring.MAX_REQUEST_BYTES:
                raise ai_coloring.ColoringError("參考圖資料超過大小限制或為空。", 413)
            raw = self.rfile.read(length)
            request = json.loads(raw)
            job_id = request["request_id"]
            fingerprint = hashlib.sha256(raw).hexdigest()
            existing = find_existing_ai_job(job_id, fingerprint)
            if existing:
                return self.send_json(existing, 202)
            session_id = request["session_id"]
            revision = request["revision"]
            session = SESSIONS.get(session_id, {}).copy()
            if not session:
                raise ai_coloring.ColoringError("分區工作階段已失效，請重新載入圖片。", 404)
            if session["revision"] != revision:
                raise ai_coloring.ColoringError("分區已更新，請對目前的分區重新分析。", 409)
            connection = self.server.ai_connection.snapshot()

            def run() -> dict:
                result = ai_coloring.analyze(session, request["references"], connection)
                if SESSIONS.get(session_id, {}).get("revision") != revision:
                    raise ai_coloring.ColoringError("分區已更新；這次判讀結果未套用，請對目前分區重新分析。", 409)
                return {"revision": revision, **result}

            job = enqueue_ai_job(job_id, fingerprint, "coloring", run)
        except ai_coloring.ColoringError as error:
            return self.send_json({"error": str(error), "code": error.code,
                                   "upstream_status": error.upstream_status}, error.status)
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "參考圖請求格式無效。", "code": "invalid_request"}, 400)
        return self.send_json(job, 202)

    def test_ai(self) -> None:
        if not self.local_ai_request():
            return self.send_json({"error": "API 測試只接受本機網頁的請求。"}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "API 測試請求格式無效。"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ai_coloring.ColoringError("API 測試請求格式無效。", 400)
            raw = self.rfile.read(length)
            request = json.loads(raw)
            job_id = request["request_id"]
            fingerprint = hashlib.sha256(raw).hexdigest()
            existing = find_existing_ai_job(job_id, fingerprint)
            if existing:
                return self.send_json(existing, 202)
            connection = self.server.ai_connection.snapshot()

            def run() -> dict:
                payload = ai_coloring.probe_request(connection["model"], connection["api_format"],
                                                    connection["output_format"])
                path = "responses" if connection["api_format"] == "responses" else "chat/completions"
                response = ai_coloring.upstream_json(connection, path, payload)
                assignments = ai_coloring.read_assignments(response, {1}, connection["api_format"])
                group = next((item for item in assignments["color_groups"] if 1 in item["region_ids"]), None)
                if group:
                    message = f"圖片 API 測試成功：合成色塊判讀為 {group['color']}（{group['name']}）。"
                elif 1 in assignments["background_ids"]:
                    message = "API 收到合成圖片並回傳有效格式，但模型將色塊判為背景。"
                else:
                    message = "API 收到合成圖片並回傳有效格式，但模型無法判定色塊顏色。"
                return {"message": message, "assignments": assignments}

            job = enqueue_ai_job(job_id, fingerprint, "probe", run)
        except ai_coloring.ColoringError as error:
            return self.send_json({"error": str(error), "code": error.code,
                                   "upstream_status": error.upstream_status}, error.status)
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "API 測試請求格式無效。", "code": "invalid_request"}, 400)
        return self.send_json(job, 202)



def main() -> None:
    parser = argparse.ArgumentParser(description="Local line-art coloring prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--unoq-render-port", type=int, help="UNO Q renderer's locally forwarded ADB port")
    parser.add_argument("--open-browser", action="store_true", help="Open the workbench after the server starts listening")
    args = parser.parse_args()
    if args.unoq_render_port is not None and not 1 <= args.unoq_render_port <= 65535:
        parser.error("--unoq-render-port must be between 1 and 65535")

    try:
        server = WorkbenchHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        parser.exit(1, f"無法在 {args.host}:{args.port} 啟動伺服器：{error}\n")

    server.board_renderer = BoardBridge(args.unoq_render_port) if args.unoq_render_port else None
    server.ai_connection = ai_coloring.APIConnection()
    url = f"http://{args.host}:{server.server_port}"
    print(f"線稿填色原型已啟動：{url}")
    print("按 Ctrl+C 停止。按下「依參考圖片上色」才會將線稿及所選參考圖片送至設定的服務。")
    try:
        if args.open_browser:
            try:
                opened = webbrowser.open(url)
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                print(f"無法自動開啟瀏覽器，請手動開啟 {url}。")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
