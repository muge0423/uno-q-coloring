"""Send an image revision and region colors to the UNO Q's forwarded USB port."""

import http.client
import json
import re
import threading
import time
import zlib

from adreno import COLOR_BYTES, MAX_PIXELS


class BoardError(RuntimeError):
    pass


def color_table(session, colors, transparent):
    if not isinstance(colors, dict) or not isinstance(transparent, list):
        raise ValueError("Invalid color assignments")
    ids = {region["id"] for region in session["regions"]}
    table = bytearray([255] * COLOR_BYTES)
    for key, value in colors.items():
        region = int(key)
        if region not in ids or not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Invalid region ID or color")
        table[4 * region:4 * region + 3] = bytes.fromhex(value[1:])
    for region in transparent:
        if type(region) is not int or region not in ids:
            raise ValueError("Invalid transparent region ID")
        table[4 * region + 3] = 0
    return table


class BoardBridge:
    def __init__(self, port):
        self.port = port
        self.lock = threading.Lock()

    def request(self, method, path, data=None, headers=None, *, timeout=8):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request(method, path, body=data, headers=headers or {})
            with connection.getresponse() as response:
                return response.status, response.headers, response.read()
        except (OSError, http.client.HTTPException) as error:
            raise BoardError("裝置無法連線。") from error
        finally:
            connection.close()

    def config(self):
        try:
            status, _, data = self.request("GET", "/health", timeout=1.5)
            renderer = json.loads(data)["renderer"] if status == 200 else None
        except (BoardError, ValueError, KeyError, TypeError):
            renderer = None
        return {"enabled": True, "renderer": renderer}

    def render(self, session, table):
        gray, labels = session["gray"], session["labels"]
        height, width = gray.shape
        if gray.size > MAX_PIXELS:
            raise BoardError("目前僅支援合成 1600 萬像素以內的圖片。")

        with self.lock:
            revision = {"X-Raster-Id": session["revision"]}
            render = lambda: self.request("POST", "/render", table, revision)
            started = time.perf_counter()
            status, headers, payload = render()
            upload_ms = None
            if status == 409:
                # Upload only when the board has not seen this exact revision (or was restarted).
                compressor = zlib.compressobj(level=1)
                planes = compressor.compress(labels) + compressor.compress(gray) + compressor.flush()
                raster_status, _, _ = self.request("PUT", "/raster", planes, {
                    **revision, "X-Raster-Width": str(width), "X-Raster-Height": str(height),
                })
                if raster_status != 200:
                    raise BoardError("裝置無法載入這張區域圖。")
                upload_ms = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                status, headers, payload = render()
            renderer = headers.get("X-Color-Renderer")
            if status != 200 or not renderer or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                raise BoardError("裝置未能完成加速合成。")
            timing = headers.get("Server-Timing", "")
            return payload, {
                "X-Color-Renderer": renderer,
                "X-UNOQ-Raster-Uploaded": "1" if upload_ms is not None else "0",
                "Server-Timing": ", ".join(filter(None, (
                    timing, f"board;dur={(time.perf_counter() - started) * 1000:.2f}",
                    f"raster;dur={upload_ms:.2f}" if upload_ms is not None else "",
                ))),
            }
