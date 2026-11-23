"""Offscreen GLES compositor for the UNO Q's Debian side. No Python add-ons."""

import ctypes as c
import re
import time


VERTEX = b"""#version 300 es
void main() {
    gl_Position = vec4(gl_VertexID == 1 ? 3.0 : -1.0,
                       gl_VertexID == 2 ? 3.0 : -1.0, 0.0, 1.0);
}
"""

FRAGMENT = b"""#version 300 es
precision highp float;
precision highp int;
uniform highp usampler2D labels;
uniform highp usampler2D lineArt;
uniform highp usampler2D regionColors;
out vec4 pixel;
void main() {
    ivec2 xy = ivec2(gl_FragCoord.xy);
    uint id = texelFetch(labels, xy, 0).r;
    uint gray = texelFetch(lineArt, xy, 0).r;
    if (id == 0u) {
        pixel = vec4(0.0, 0.0, 0.0, float(255u - gray) / 255.0);
    } else {
        uvec4 color = texelFetch(regionColors, ivec2(int(id), 0), 0);
        uvec3 rgb = color.a == 0u ? uvec3(0) : (color.rgb * gray + 127u) / 255u;
        pixel = vec4(vec3(rgb), float(color.a)) / 255.0;
    }
}
"""

MAX_PIXELS = 16_000_000
COLOR_BYTES = 256 * 4

EGL_NONE = 0x3038
EGL_PLATFORM_SURFACELESS_MESA = 0x31DD
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_R8UI, GL_RED_INTEGER = 0x8232, 0x8D94
GL_RGBA8UI, GL_RGBA_INTEGER = 0x8D7C, 0x8D99
GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE = 0x8058, 0x1908, 0x1401
GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0 = 0x8D40, 0x8CE0


def bind(library, signatures):
    for name, (result, *arguments) in signatures.items():
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
    return library


class Adreno:
    def __init__(self):
        int_, uint, ptr, char = c.c_int, c.c_uint, c.c_void_p, c.c_char_p
        ints, uints = c.POINTER(int_), c.POINTER(uint)
        egl = bind(c.CDLL("libEGL.so.1"), {
            "eglGetPlatformDisplay": (ptr, uint, ptr, ptr),
            "eglInitialize": (uint, ptr, ints, ints),
            "eglBindAPI": (uint, uint),
            "eglChooseConfig": (uint, ptr, ints, c.POINTER(ptr), int_, ints),
            "eglCreatePbufferSurface": (ptr, ptr, ptr, ints),
            "eglCreateContext": (ptr, ptr, ptr, ptr, ints),
            "eglMakeCurrent": (uint, ptr, ptr, ptr, ptr),
            "eglGetError": (int_,),
        })
        self.gles = gl = bind(c.CDLL("libGLESv2.so.2"), {
            "glGetString": (char, uint), "glGetError": (uint,),
            "glGetIntegerv": (None, uint, ints), "glDisable": (None, uint),
            "glPixelStorei": (None, uint, int_),
            "glCreateShader": (uint, uint), "glCompileShader": (None, uint),
            "glShaderSource": (None, uint, int_, c.POINTER(char), ints),
            "glGetShaderiv": (None, uint, uint, ints),
            "glGetShaderInfoLog": (None, uint, int_, ints, ptr),
            "glDeleteShader": (None, uint), "glCreateProgram": (uint,),
            "glAttachShader": (None, uint, uint), "glLinkProgram": (None, uint),
            "glGetProgramiv": (None, uint, uint, ints),
            "glGetProgramInfoLog": (None, uint, int_, ints, ptr),
            "glUseProgram": (None, uint),
            "glGetUniformLocation": (int_, uint, char), "glUniform1i": (None, int_, int_),
            "glGenTextures": (None, int_, uints), "glActiveTexture": (None, uint),
            "glBindTexture": (None, uint, uint), "glTexParameteri": (None, uint, uint, int_),
            "glTexImage2D": (None, uint, int_, int_, int_, int_, int_, uint, uint, ptr),
            "glTexSubImage2D": (None, uint, int_, int_, int_, int_, int_, uint, uint, ptr),
            "glGenFramebuffers": (None, int_, uints), "glBindFramebuffer": (None, uint, uint),
            "glFramebufferTexture2D": (None, uint, uint, uint, uint, int_),
            "glCheckFramebufferStatus": (uint, uint),
            "glViewport": (None, int_, int_, int_, int_),
            "glDrawArrays": (None, uint, int_, int_), "glFinish": (None,),
            "glReadPixels": (None, int_, int_, int_, int_, uint, uint, ptr),
        })

        def attributes(*values):
            return (int_ * (len(values) + 1))(*values, EGL_NONE)

        def check(result, name):
            if not result:
                raise RuntimeError(f"{name}: EGL error {egl.eglGetError():#x}")
            return result

        # EGL pbuffer keeps OpenGL independent of the UNO Q desktop session.
        display = check(egl.eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, None, None),
                        "eglGetPlatformDisplay")
        check(egl.eglInitialize(display, None, None), "eglInitialize")
        check(egl.eglBindAPI(0x30A0), "eglBindAPI(OpenGL ES)")
        config, count = ptr(), int_()
        check(egl.eglChooseConfig(display, attributes(0x3033, 1, 0x3040, 0x40),
                                  c.byref(config), 1, c.byref(count)) and count.value,
              "eglChooseConfig(ES3 pbuffer)")
        surface = check(egl.eglCreatePbufferSurface(display, config, attributes(0x3057, 1, 0x3056, 1)),
                        "eglCreatePbufferSurface")
        context = check(egl.eglCreateContext(display, config, None, attributes(0x3098, 3)), "eglCreateContext")
        check(egl.eglMakeCurrent(display, surface, surface, context), "eglMakeCurrent")
        self.egl = egl

        self.renderer = gl.glGetString(0x1F01).decode("ascii", "replace")  # GL_RENDERER
        self.version = gl.glGetString(0x1F02).decode("ascii", "replace")   # GL_VERSION
        if not re.search(r"\b(?:FD702|Adreno(?:\s*\(TM\))?\s+702)\b", self.renderer, re.I):
            raise RuntimeError(f"Expected the physical Adreno 702; got {self.renderer}")
        size, viewport = int_(), (int_ * 2)()
        gl.glGetIntegerv(0x0D33, c.byref(size))  # MAX_TEXTURE_SIZE
        gl.glGetIntegerv(0x0D3A, viewport)      # MAX_VIEWPORT_DIMS
        self.max_width, self.max_height = (min(size.value, dim) for dim in viewport)
        program = self.link_program()
        gl.glUseProgram(program)
        gl.glDisable(0x0BD0)  # DITHER: preserve the rounded bytes in the RGBA8 target.
        gl.glPixelStorei(0x0CF5, 1)  # UNPACK_ALIGNMENT: labels can have an odd row width.
        gl.glPixelStorei(0x0D05, 1)  # PACK_ALIGNMENT
        self.textures = (uint * 4)()
        gl.glGenTextures(4, self.textures)
        for slot in range(4):
            self.bind_texture(slot)
            gl.glTexParameteri(GL_TEXTURE_2D, 0x2801, 0x2600)  # MIN_FILTER = NEAREST
            gl.glTexParameteri(GL_TEXTURE_2D, 0x2800, 0x2600)  # MAG_FILTER = NEAREST
        for slot, name in enumerate((b"labels", b"lineArt", b"regionColors")):
            gl.glUniform1i(gl.glGetUniformLocation(program, name), slot)
        self.texture(2, GL_RGBA8UI, GL_RGBA_INTEGER, 256, 1)
        self.fbo = uint()
        gl.glGenFramebuffers(1, c.byref(self.fbo))
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        self.check_gl("initialization")

    def link_program(self):
        gl = self.gles
        program = gl.glCreateProgram()
        shaders = []
        for kind, data in ((0x8B31, VERTEX), (0x8B30, FRAGMENT)):
            shader = gl.glCreateShader(kind)
            shaders.append(shader)
            gl.glShaderSource(shader, 1, c.byref(c.c_char_p(data)), None)
            gl.glCompileShader(shader)
            self.check_program(shader, 0x8B81, gl.glGetShaderiv, gl.glGetShaderInfoLog)
            gl.glAttachShader(program, shader)
        gl.glLinkProgram(program)
        self.check_program(program, 0x8B82, gl.glGetProgramiv, gl.glGetProgramInfoLog)
        for shader in shaders:
            gl.glDeleteShader(shader)
        return program

    @staticmethod
    def check_program(handle, status, getter, describe):
        ok = c.c_int()
        getter(handle, status, c.byref(ok))
        if not ok.value:
            log = c.create_string_buffer(4096)
            describe(handle, len(log), None, log)
            raise RuntimeError(log.value.decode("utf-8", "replace"))

    def check_gl(self, step):
        errors = []
        while (code := self.gles.glGetError()) != 0:
            errors.append(hex(code))
        if errors:
            raise RuntimeError(f"{step}: OpenGL errors {', '.join(errors)}")

    def bind_texture(self, slot):
        self.gles.glActiveTexture(GL_TEXTURE0 + slot)
        self.gles.glBindTexture(GL_TEXTURE_2D, self.textures[slot])

    def texture(self, slot, internal, external, width, height, data=None):
        self.bind_texture(slot)
        self.gles.glTexImage2D(GL_TEXTURE_2D, 0, internal, width, height, 0, external, GL_UNSIGNED_BYTE, data)

    def load(self, width, height, planes):
        size = width * height
        if (not 0 < width <= self.max_width or not 0 < height <= self.max_height or
                not 0 < size <= MAX_PIXELS or len(planes) != 2 * size):
            raise ValueError("Raster exceeds renderer limits or does not match the image size")
        self.texture(0, GL_R8UI, GL_RED_INTEGER, width, height, planes[:size])
        self.texture(1, GL_R8UI, GL_RED_INTEGER, width, height, planes[size:])
        self.texture(3, GL_RGBA8, GL_RGBA, width, height)
        gl = self.gles
        gl.glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.textures[3], 0)
        self.check_gl("upload")
        if gl.glCheckFramebufferStatus(GL_FRAMEBUFFER) != 0x8CD5:
            raise RuntimeError("Adreno framebuffer is incomplete")
        self.width, self.height = width, height

    def render(self, colors):
        if len(colors) != COLOR_BYTES:
            raise ValueError("The region color table must contain 256 RGBA entries")
        gl = self.gles
        started = time.perf_counter()
        self.bind_texture(2)
        gl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 256, 1, GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, colors)
        gl.glViewport(0, 0, self.width, self.height)
        gl.glDrawArrays(0x0004, 0, 3)  # TRIANGLES
        gl.glFinish()
        drawn = time.perf_counter()
        # Upload and readback both put texel row 0 first. Rows cancel the GL Y convention.
        rgba = bytearray(4 * self.width * self.height)
        pixels = (c.c_ubyte * len(rgba)).from_buffer(rgba)
        gl.glReadPixels(0, 0, self.width, self.height, GL_RGBA, GL_UNSIGNED_BYTE, pixels)
        self.check_gl("draw/readback")
        return rgba, (drawn - started) * 1000, (time.perf_counter() - drawn) * 1000

    def self_test(self):
        # Odd-width asymmetric layout detects row alignment and orientation bugs.
        self.load(3, 2, bytes([0, 1, 1, 254, 2, 0, 245, 255, 128, 111, 75, 0]))
        table = bytearray([255] * COLOR_BYTES)
        table[4:8] = bytes([255, 100, 20, 255])
        table[8:12] = bytes([0, 0, 0, 0])
        rgba, *_ = self.render(bytes(table))
        expected = bytes([0, 0, 0, 10, 255, 100, 20, 255, 128, 50, 10, 255,
                          111, 111, 111, 255, 0, 0, 0, 0, 0, 0, 0, 255])
        if rgba != expected:
            raise RuntimeError(f"Color/alpha/orientation self-test failed: {rgba.hex()} != {expected.hex()}")


if __name__ == "__main__":
    gpu = Adreno()
    gpu.self_test()
    print(f"{gpu.renderer} / {gpu.version} / {gpu.max_width}x{gpu.max_height}; RGBA self-test passed")
