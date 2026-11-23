import base64
import http.client
import io
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from unittest.mock import patch

import cv2
import numpy as np

import ai_coloring


def model_response(groups=(), background=(), uncertain=()):
    reply = dict(color_groups=list(groups), background_ids=list(background), uncertain_ids=list(uncertain))
    return {"status": "completed", "output": [{"type": "reasoning"}, {
        "type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]
    }]}


class AutoColorTests(unittest.TestCase):
    def test_prompt_explains_enclosed_negative_space_as_background(self):
        gray = np.full((24, 24), 255, dtype=np.uint8)
        labels = np.ones_like(gray)
        regions = [{"id": 1, "area": 576, "bbox": [0, 0, 24, 24],
                    "center": [12, 12], "touches_border": False}]
        reference = np.full((8, 8, 3), (20, 120, 200), dtype=np.uint8)

        for api_format in ("responses", "chat_completions"):
            with self.subTest(api_format=api_format):
                payload = ai_coloring.build_request("vision", gray, labels, regions, [reference], api_format)
                instructions = (payload["instructions"] if api_format == "responses"
                                else payload["messages"][0]["content"])
                self.assertIn("enclosed negative space", instructions.lower())
                self.assertIn("transparent background", instructions.lower())
                self.assertIn("outer silhouette", instructions.lower())

    def test_reference_is_only_color_image_in_request(self):
        gray = np.full((90, 90), 255, dtype=np.uint8)
        labels = np.zeros_like(gray)
        labels[32:58, 32:58] = 1
        labels[65:70, 65:70] = 2
        regions = [
            {"id": 1, "area": 676, "bbox": [32, 32, 26, 26], "center": [45, 45], "touches_border": False},
            {"id": 2, "area": 25, "bbox": [65, 65, 5, 5], "center": [67, 67], "touches_border": False},
        ]
        session = dict(gray=gray, labels=labels, regions=regions, color_map={1: "#123456"})
        reference = np.full((12, 34, 3), (200, 80, 30), dtype=np.uint8)
        encoded = base64.b64encode(cv2.imencode(".png", reference)[1]).decode("ascii")
        expected = model_response([{"name": "shared", "color": "#2288AA", "region_ids": [1]}])

        with patch.object(ai_coloring, "settings", return_value=("test-placeholder", "test-model")), \
                patch.object(ai_coloring, "open_upstream", return_value=io.BytesIO(json.dumps(expected).encode())) as send:
            result = ai_coloring.analyze(session, [encoded])

        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-placeholder")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        text = payload["instructions"] + " ".join(part["text"] for part in payload["input"][0]["content"] if part["type"] == "input_text")
        self.assertNotIn("#123456", text)
        self.assertNotRegex(text, r"#[0-9a-fA-F]{6}\b")
        images = [cv2.imdecode(np.frombuffer(base64.b64decode(part["image_url"].split(",", 1)[1]), dtype=np.uint8),
                              cv2.IMREAD_UNCHANGED)
                  for part in payload["input"][0]["content"] if part["type"] == "input_image"]
        self.assertEqual([img.ndim for img in images], [3, 2, 2, 2])
        self.assertTrue(np.allclose(images[0], reference, atol=3))
        self.assertIn(130, images[-1])
        self.assertEqual(result["color_groups"], [{"name": "shared", "color": "#2288aa", "region_ids": [1]}])
        self.assertEqual(result["uncertain_ids"], [2])

    def test_model_cannot_assign_invalid_or_duplicate_regions(self):
        for color, assigned, background in [("#aabbcc", [4], []), ("#aabbcc", [1], [1]), ("red", [1], []),
                                             ("#aabbcc", [True], [])]:
            with self.subTest(color=color, assigned=assigned, background=background):
                reply = model_response([{"name": "part", "color": color, "region_ids": assigned}], background)
                with self.assertRaises(ai_coloring.ColoringError):
                    ai_coloring.read_assignments(reply, {1, 2, 3})

    def test_invalid_reference_never_calls_the_model(self):
        with patch.object(ai_coloring, "settings", return_value=("test-placeholder", "test-model")), \
                patch.object(ai_coloring, "open_upstream") as send:
            with self.assertRaises(ai_coloring.ColoringError):
                ai_coloring.analyze({"regions": [{"id": 1}], "gray": None, "labels": None}, ["not base64"])
        send.assert_not_called()

    def test_chat_request_has_multimodal_images_and_no_user_palette(self):
        gray = np.full((90, 90), 255, dtype=np.uint8)
        labels = np.zeros_like(gray)
        labels[32:58, 32:58] = 1
        regions = [{"id": 1, "area": 676, "bbox": [32, 32, 26, 26], "center": [45, 45], "touches_border": False}]
        session = dict(gray=gray, labels=labels, regions=regions, color_map={1: "#123456"})
        reference = np.full((12, 34, 3), (200, 80, 30), dtype=np.uint8)
        encoded = base64.b64encode(cv2.imencode(".png", reference)[1]).decode("ascii")
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "color_groups": [{"name": "shared", "color": "#2288AA", "region_ids": [1]}],
            "background_ids": [], "uncertain_ids": []})}}]}
        connection = {"api_key": "local-test-key", "key_source": "session", "model": "custom-vision-model",
                      "base_url": "https://relay.example/v1", "api_format": "chat_completions",
                      "output_format": "json_schema"}
        with patch.object(ai_coloring, "open_upstream", return_value=io.BytesIO(json.dumps(reply).encode())) as send:
            result = ai_coloring.analyze(session, [encoded], connection)
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://relay.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer local-test-key")
        self.assertEqual(payload["model"], "custom-vision-model")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        content = payload["messages"][1]["content"]
        self.assertEqual(sum(part["type"] == "image_url" for part in content), 3)
        self.assertTrue(all(part["image_url"]["url"].startswith("data:image/")
                            for part in content if part["type"] == "image_url"))
        self.assertNotIn("#123456", json.dumps(payload))
        self.assertEqual(result["color_groups"][0]["color"], "#2288aa")

    def test_chat_compatible_output_formats_and_validation(self):
        gray = np.full((30, 30), 255, dtype=np.uint8)
        region = {"id": 1, "area": 400, "bbox": [5, 5, 20, 20], "center": [15, 15], "touches_border": False}
        for api_format in ai_coloring.API_FORMATS:
            for output_format in ("json_object", "text"):
                with self.subTest(api_format=api_format, output_format=output_format):
                    payload = ai_coloring.build_request("vision", gray, np.zeros_like(gray), [region], [gray],
                                                        api_format, output_format)
                    if api_format == "chat_completions":
                        self.assertEqual(payload.get("response_format"), {"type": "json_object"} if output_format == "json_object" else None)
                        content = payload["messages"][1]["content"]
                        text = " ".join(p["text"] for p in content if p["type"] == "text")
                    else:
                        self.assertEqual(payload.get("text"), {"format": {"type": "json_object"}} if output_format == "json_object" else None)
                        content = payload["input"][0]["content"]
                        text = " ".join(p["text"] for p in content if p["type"] == "input_text")
                    self.assertIn("color_groups", text)
        invalid = {"choices": [{"finish_reason": "stop", "message": {"content":
                   '{"color_groups":[{"name":"x","color":"#112233","region_ids":[99]}],"background_ids":[],"uncertain_ids":[]}'}}]}
        with self.assertRaises(ai_coloring.ColoringError):
            ai_coloring.read_assignments(invalid, {1}, "chat_completions")

    def test_model_listing_uses_saved_url_and_key(self):
        connection = {"api_key": "local-test-key", "base_url": "https://relay.example/v1"}
        reply = {"data": [{"id": "vision-b"}, {"id": "vision-a"}, {"id": "vision-a"}, {"id": 1}]}
        with patch.object(ai_coloring, "open_upstream", return_value=io.BytesIO(json.dumps(reply).encode())) as send:
            self.assertEqual(ai_coloring.list_models(connection), ["vision-a", "vision-b"])
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://relay.example/v1/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer local-test-key")

    def test_connection_keeps_key_private_and_requires_key_for_new_host(self):
        with patch.dict(os.environ, {}, clear=True):
            connection = ai_coloring.APIConnection()
        connection.update({"base_url": "https://relay.example/v1/", "api_key": "local-test-key", "model": "manual-vision"})
        self.assertNotIn("local-test-key", json.dumps(connection.public()))
        self.assertEqual(connection.snapshot()["api_key"], "local-test-key")
        connection.update({"api_key": "", "model": "different-model"})
        self.assertEqual(connection.snapshot()["api_key"], "local-test-key")
        with self.assertRaises(ai_coloring.ColoringError):
            connection.update({"base_url": "https://other.example/v1"})
        self.assertEqual(connection.snapshot()["base_url"], "https://relay.example/v1")
        connection.update({"base_url": "https://other.example/v1", "clear_api_key": True})
        self.assertFalse(connection.public()["configured"])

    def test_base_url_validation(self):
        for url in ("https://example.com/v1?key=secret", "https://user:secret@example.com/v1",
                    "http://example.com/v1", "file:///tmp/api", "https://example.com:bad/v1"):
            with self.subTest(url=url), self.assertRaises(ai_coloring.ColoringError):
                ai_coloring.normalize_base_url(url)
        self.assertEqual(ai_coloring.normalize_base_url("http://127.0.0.1:8000/v1/"), "http://127.0.0.1:8000/v1")

    def test_upstream_errors_do_not_reveal_key_or_response_body(self):
        connection = {"api_key": "local-test-key", "base_url": "https://relay.example/v1"}
        failure = HTTPError("https://relay.example/v1/models", 401, "unauthorized local-test-key", {},
                            io.BytesIO(b"local-test-key"))
        with patch.object(ai_coloring, "open_upstream", side_effect=failure):
            with self.assertRaises(ai_coloring.ColoringError) as raised:
                ai_coloring.list_models(connection)
        self.assertNotIn("local-test-key", str(raised.exception))


    def test_truncated_upstream_response_is_classified_as_disconnect(self):
        class TruncatedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                raise http.client.IncompleteRead(b'{"partial":', 25)

        connection = {"api_key": "local-test-key", "base_url": "https://relay.example/v1"}
        with patch.object(ai_coloring, "open_upstream", return_value=TruncatedResponse()):
            with self.assertRaises(ai_coloring.ColoringError) as raised:
                ai_coloring.upstream_json(connection, "responses", {})
        self.assertEqual(raised.exception.code, "upstream_disconnected")
        self.assertEqual(raised.exception.status, 502)
        self.assertNotIn("local-test-key", str(raised.exception))

    def test_upstream_timeout_has_specific_safe_error(self):
        connection = {"api_key": "local-test-key", "base_url": "https://relay.example/v1"}
        with patch.object(ai_coloring, "open_upstream", side_effect=TimeoutError):
            with self.assertRaises(ai_coloring.ColoringError) as raised:
                ai_coloring.upstream_json(connection, "responses", {})
        self.assertEqual(raised.exception.code, "upstream_timeout")
        self.assertEqual(raised.exception.status, 504)
        self.assertNotIn("local-test-key", str(raised.exception))

    def test_schema_rejection_is_identified_without_leaking_upstream_body(self):
        connection = {"api_key": "local-test-key", "base_url": "https://relay.example/v1"}
        detail = json.dumps({"error": {"param": "text.format", "message": "private relay detail"}}).encode()
        failure = HTTPError("https://relay.example/v1/responses", 400, "bad request", {}, io.BytesIO(detail))
        with patch.object(ai_coloring, "open_upstream", side_effect=failure):
            with self.assertRaises(ai_coloring.ColoringError) as raised:
                ai_coloring.upstream_json(connection, "responses", {})
        self.assertEqual(raised.exception.code, "upstream_format")
        self.assertEqual(raised.exception.upstream_status, 400)
        self.assertIn("JSON 物件", str(raised.exception))
        self.assertNotIn("private relay detail", str(raised.exception))

class MockRelayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def reply(self, result):
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("Authorization"), None))
        self.reply({"data": [{"id": "relay-vision"}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, self.headers.get("Authorization"), payload))
        is_probe = "synthetic connection test" in json.dumps(payload)
        target_id = 1 if is_probe else 2
        assignments = {"color_groups": [{"name": "合成色塊" if is_probe else "衣服",
                                           "color": "#654321", "region_ids": [target_id]}],
                       "background_ids": [] if is_probe else [1], "uncertain_ids": []}
        if self.path.endswith("chat/completions"):
            self.reply({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(assignments)}}]})
        else:
            self.reply(model_response(assignments["color_groups"], assignments["background_ids"]))


class LocalServerIntegrationTests(unittest.TestCase):
    def test_config_models_and_both_image_apis_over_http(self):
        import server

        relay = ThreadingHTTPServer(("127.0.0.1", 0), MockRelayHandler)
        relay.requests = []
        app = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        with patch.dict(os.environ, {}, clear=True):
            app.ai_connection = ai_coloring.APIConnection()
        app.board_renderer = None
        app.sample_path = None
        threads = [threading.Thread(target=instance.serve_forever, daemon=True) for instance in (relay, app)]
        for thread in threads:
            thread.start()
        from urllib.request import Request, urlopen

        app_url = f"http://127.0.0.1:{app.server_port}"
        relay_url = f"http://127.0.0.1:{relay.server_port}/v1"

        def call(path, payload=None, *, host=None, origin=None):
            headers = {"Content-Type": "application/json"}
            if host:
                headers["Host"] = host
            if origin:
                headers["Origin"] = origin
            request = Request(app_url + path, data=json.dumps(payload).encode() if payload is not None else None,
                              headers=headers, method="POST" if payload is not None else "GET")
            with urlopen(request, timeout=10) as response:
                return json.load(response)

        def wait_for_job(job_id, initial):
            job = initial
            deadline = time.monotonic() + 10
            while job["status"] in ("queued", "running"):
                if time.monotonic() >= deadline:
                    self.fail(f"AI job {job_id} did not finish")
                time.sleep(0.01)
                job = call(f"/api/auto-color/jobs/{job_id}")
            return job

        try:
            original = call("/api/auto-color/config")
            self.assertFalse(original["configured"])
            with self.assertRaises(HTTPError) as denied:
                call("/api/auto-color/config", host="127.0.0.1", origin="https://evil.example")
            self.assertEqual(denied.exception.code, 403)
            config = {"api_key": "integration-only-test-key", "base_url": relay_url, "model": "manual-vision",
                      "api_format": "responses", "output_format": "json_schema"}
            public = call("/api/auto-color/config", config)
            self.assertTrue(public["configured"])
            self.assertNotIn("integration-only-test-key", json.dumps(public))
            self.assertEqual(call("/api/auto-color/models", {})["models"], ["relay-vision"])

            gray = np.full((90, 90), 255, dtype=np.uint8)
            labels = np.zeros_like(gray)
            labels[32:58, 32:58] = 2
            regions = [
                {"id": 1, "area": 7000, "bbox": [0, 0, 90, 90], "center": [10, 10], "touches_border": True},
                {"id": 2, "area": 676, "bbox": [32, 32, 26, 26], "center": [45, 45], "touches_border": False},
            ]
            session_id, revision = "integration-test-session", "revision-1"
            server.SESSIONS[session_id] = {"gray": gray, "labels": labels, "regions": regions,
                                           "revision": revision, "color_map": {2: "#123456"}}
            image = np.full((12, 34, 3), (200, 80, 30), dtype=np.uint8)
            reference = base64.b64encode(cv2.imencode(".png", image)[1]).decode("ascii")
            for api_format, suffix in (("responses", "1"), ("chat_completions", "2")):
                call("/api/auto-color/config", {"api_format": api_format, "api_key": ""})
                request_id = "a" * 31 + suffix
                request_payload = {"request_id": request_id, "session_id": session_id,
                                   "revision": revision, "references": [reference]}
                started = call("/api/auto-color", request_payload)
                duplicate = call("/api/auto-color", request_payload)
                self.assertEqual(started["job_id"], duplicate["job_id"])
                job = wait_for_job(request_id, started)
                self.assertEqual(job["status"], "completed")
                self.assertEqual(job["result"]["color_groups"][0]["color"], "#654321")
                self.assertEqual(job["result"]["background_ids"], [1])

            call("/api/auto-color/config", {"api_format": "responses", "api_key": ""})
            probe_id = "b" * 32
            probe_payload = {"request_id": probe_id}
            started = call("/api/auto-color/test", probe_payload)
            duplicate = call("/api/auto-color/test", probe_payload)
            self.assertEqual(started["job_id"], duplicate["job_id"])
            probe_job = wait_for_job(probe_id, started)
            self.assertEqual(probe_job["status"], "completed")
            self.assertIn("測試成功", probe_job["result"]["message"])
            probe_request = relay.requests[-1][2]
            probe_content = probe_request["input"][0]["content"]
            self.assertIn("synthetic connection test", " ".join(
                part["text"] for part in probe_content if part["type"] == "input_text"))
            self.assertEqual(sum(part["type"] == "input_image" for part in probe_content), 3)
            self.assertEqual([r[0] for r in relay.requests],
                             ["/v1/models", "/v1/responses", "/v1/chat/completions", "/v1/responses"])
            self.assertTrue(all(r[1] == "Bearer integration-only-test-key" for r in relay.requests))
            self.assertTrue(all("#123456" not in json.dumps(r[2]) for r in relay.requests if r[2]))
        finally:
            server.SESSIONS.pop("integration-test-session", None)
            for instance in (relay, app):
                instance.shutdown()
                instance.server_close()
            for thread in threads:
                thread.join()

    def test_upstream_ordinary_4xx_does_not_expose_response_body(self):
        # The response body could include a provider's internal details or token.
        key = "fake-demo-key"
        connection = {"api_key": key, "base_url": "https://relay.example/v1"}
        for code in (400, 401, 403, 404, 405, 422, 429, 500):
            with self.subTest(code=code):
                failure = HTTPError("https://relay.example/v1/models", code, key, {}, io.BytesIO(key.encode()))
                with patch.object(ai_coloring, "open_upstream", side_effect=failure):
                    with self.assertRaises(ai_coloring.ColoringError) as raised:
                        ai_coloring.list_models(connection)
                self.assertNotIn(key, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
