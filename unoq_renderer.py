"""Loopback-only render service for the UNO Q, intended for USB ADB forwarding."""

import argparse
import json
import re
import struct
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

from adreno import Adreno, COLOR_BYTES, MAX_PIXELS


def rgba_png(width, height, pixels):
    def chunk(kind, data):
        checksum = zlib.crc32(data, zlib.crc32(kind))
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    compressor = zlib.compressobj(level=1)
    compressed = []
    stride = width * 4
    view = memoryview(pixels)
    for offset in range(0, len(view), stride):
        compressed.append(compressor.compress(b"\0"))  # PNG filter: None.
        compressed.append(compressor.compress(view[offset:offset + stride]))
    compressed.append(compressor.flush())
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", b"".join(compressed)) + chunk(b"IEND", b""))


class RenderHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def send(self, data, content_type, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.send(json.dumps(value).encode(), "application/json", status)

    def do_GET(self):
        if self.path != "/health":
            return self.json({"error": "Unknown route"}, 404)
        gpu = self.server.gpu
        self.json({"renderer": gpu.renderer, "version": gpu.version})

    def do_PUT(self):
        if self.path != "/raster":
            return self.json({"error": "Unknown route"}, 404)
        try:
            revision = self.revision()
            width = int(self.headers["X-Raster-Width"])
            height = int(self.headers["X-Raster-Height"])
            pixel_count = width * height
            if not (0 < width <= self.server.gpu.max_width and 0 < height <= self.server.gpu.max_height
                    and 0 < pixel_count <= MAX_PIXELS):
                raise ValueError("Invalid or unsupported raster size")
            compressed = self.body(2 * pixel_count + 65_536)
            inflater = zlib.decompressobj()
            planes = inflater.decompress(compressed, 2 * pixel_count + 1)
            if len(planes) != 2 * pixel_count or not inflater.eof or inflater.unused_data:
                raise ValueError("Invalid compressed raster")
        except (ValueError, TypeError, zlib.error) as error:
            return self.json({"error": str(error)}, 400)

        self.server.revision = None
        try:
            self.server.gpu.load(width, height, planes)
        except (ValueError, RuntimeError) as error:
            return self.json({"error": str(error)}, 503)
        self.server.revision = revision
        self.json({"ok": True})

    def do_POST(self):
        if self.path != "/render":
            return self.json({"error": "Unknown route"}, 404)
        try:
            revision = self.revision()
            colors = self.body(COLOR_BYTES)
            if len(colors) != COLOR_BYTES:
                raise ValueError("Invalid color table")
        except (ValueError, TypeError) as error:
            return self.json({"error": str(error)}, 400)
        if self.server.revision != revision:
            return self.json({"error": "Raster not cached"}, 409)
        gpu = self.server.gpu
        try:
            rgba, draw_ms, read_ms = gpu.render(colors)
            started = time.perf_counter()
            png = rgba_png(gpu.width, gpu.height, rgba)
            png_ms = (time.perf_counter() - started) * 1000
        except RuntimeError as error:
            self.server.revision = None
            return self.json({"error": str(error)}, 503)
        self.send(png, "image/png", headers={
            "X-Color-Renderer": gpu.renderer,
            "Server-Timing": f"draw;dur={draw_ms:.2f}, readback;dur={read_ms:.2f}, png;dur={png_ms:.2f}",
        })

    def revision(self):
        revision = self.headers.get("X-Raster-Id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", revision):
            raise ValueError("Invalid raster revision")
        return revision

    def body(self, max_length):
        length = int(self.headers["Content-Length"])
        if not 0 < length <= max_length:
            raise ValueError("Invalid request length")
        return self.rfile.read(length)


def main():
    parser = argparse.ArgumentParser(description="UNO Q Adreno compositor over USB ADB forwarding")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    gpu = Adreno()
    gpu.self_test()
    # All GLES operations run on the HTTPServer thread that owns the context.
    with HTTPServer(("127.0.0.1", args.port), RenderHandler) as server:
        server.gpu, server.revision = gpu, None
        print(f"{gpu.renderer} / {gpu.version}; listening on 127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
