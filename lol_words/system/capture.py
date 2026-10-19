"""只返回游戏中央区域采样；优先 DXGI 桌面复制，失败时回退 GDI。"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import sys
import time
from .paths import ROOT


class BitmapHeader(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                ("image_size", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                ("used", W.DWORD), ("important", W.DWORD)]


class GdiCapture:
    def __init__(self):
        self.u = C.WinDLL("user32", use_last_error=True)
        self.g = C.WinDLL("gdi32", use_last_error=True)
        self.u.GetDC.argtypes = [W.HWND]
        self.u.GetDC.restype = W.HDC
        self.u.ReleaseDC.argtypes = [W.HWND, W.HDC]
        self.u.ReleaseDC.restype = C.c_int
        self.u.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        self.u.GetClientRect.restype = W.BOOL
        self.u.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
        self.u.ClientToScreen.restype = W.BOOL
        self.u.IsIconic.argtypes = [W.HWND]
        self.u.IsIconic.restype = W.BOOL
        self.g.CreateCompatibleDC.argtypes = [W.HDC]
        self.g.CreateCompatibleDC.restype = W.HDC
        self.g.CreateDIBSection.argtypes = [W.HDC, C.POINTER(BitmapHeader), W.UINT,
                                          C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
        self.g.CreateDIBSection.restype = W.HBITMAP
        self.g.SelectObject.argtypes = [W.HDC, W.HANDLE]
        self.g.SelectObject.restype = W.HANDLE
        self.g.SetStretchBltMode.argtypes = [W.HDC, C.c_int]
        self.g.SetStretchBltMode.restype = C.c_int
        self.g.StretchBlt.argtypes = [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int,
                                     W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.DWORD]
        self.g.StretchBlt.restype = W.BOOL
        self.g.DeleteObject.argtypes = [W.HANDLE]
        self.g.DeleteObject.restype = W.BOOL
        self.g.DeleteDC.argtypes = [W.HDC]
        self.g.DeleteDC.restype = W.BOOL
        self.g.GdiFlush.restype = W.BOOL

    def read(self, hwnd, roi=(0.12, 0.12, 0.88, 0.70)):
        if not hwnd or self.u.IsIconic(hwnd):
            return None
        rect, origin = W.RECT(), W.POINT(0, 0)
        if not (self.u.GetClientRect(hwnd, C.byref(rect)) and
                self.u.ClientToScreen(hwnd, C.byref(origin))):
            return None
        width, height = rect.right, rect.bottom
        if width < 300 or height < 200:
            return None
        left, top, right, bottom = roi
        x, y = origin.x + int(width * left), origin.y + int(height * top)
        sw, sh = int(width * (right - left)), int(height * (bottom - top))
        if sw <= 0 or sh <= 0:
            return None
        screen = self.u.GetDC(None)
        if not screen:
            return None
        memory = bitmap = old = None
        try:
            memory = self.g.CreateCompatibleDC(screen)
            if not memory:
                return None
            sample_w, sample_h = 96, 54
            header = BitmapHeader()
            header.size = C.sizeof(BitmapHeader)
            header.width, header.height = sample_w, -sample_h
            header.planes, header.bits = 1, 32
            bits = C.c_void_p()
            bitmap = self.g.CreateDIBSection(screen, C.byref(header), 0, C.byref(bits), None, 0)
            if not bitmap or not bits:
                return None
            old = self.g.SelectObject(memory, bitmap)
            if not old or old == C.c_void_p(-1).value:
                old = None
                return None
            self.g.SetStretchBltMode(memory, 3)
            if not self.g.StretchBlt(memory, 0, 0, sample_w, sample_h,
                                     screen, x, y, sw, sh, 0x00CC0020):
                return None
            self.g.GdiFlush()
            return C.string_at(bits, sample_w * sample_h * 4)
        finally:
            if old:
                self.g.SelectObject(memory, old)
            if bitmap:
                self.g.DeleteObject(bitmap)
            if memory:
                self.g.DeleteDC(memory)
            self.u.ReleaseDC(None, screen)


class SceneCapture:
    def __init__(self):
        self.gdi = GdiCapture()
        self.backend = "尚未采样"
        self.detail = ""
        self.module = None
        self.import_attempted = False
        self.cameras = {}
        self.retry_after = 0

    def _load_dxgi(self):
        if self.import_attempted:
            return
        self.import_attempted = True
        vendor = ROOT / "_deps"
        if vendor.is_dir() and str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        try:
            import dxcam
            self.module = dxcam
        except Exception as exc:
            self.detail = f"DXGI 不可用：{type(exc).__name__}: {exc}"

    def _region(self, hwnd, roi):
        u = self.gdi.u
        if not hwnd or u.IsIconic(hwnd):
            return None
        rect, origin = W.RECT(), W.POINT()
        if not (u.GetClientRect(hwnd, C.byref(rect)) and u.ClientToScreen(hwnd, C.byref(origin))):
            return None
        if rect.right < 300 or rect.bottom < 200:
            return None
        left, top, right, bottom = roi
        return (origin.x + int(rect.right * left), origin.y + int(rect.bottom * top),
                origin.x + int(rect.right * right), origin.y + int(rect.bottom * bottom))

    def _dxgi_read(self, region):
        # 固定 DXcam 0.3.0 的已核验输出元数据，用设备坐标适配多显示器和负坐标。
        factory = getattr(self.module, "__factory", None)
        if factory is None:
            raise RuntimeError("DXcam 输出元数据不可用")
        left, top, right, bottom = region
        for device_idx, outputs in enumerate(factory.outputs):
            for output_idx, output in enumerate(outputs):
                output.update_desc()
                bounds = output.desc.DesktopCoordinates
                if not (bounds.left <= left < right <= bounds.right and
                        bounds.top <= top < bottom <= bounds.bottom):
                    continue
                key = device_idx, output_idx
                if key not in self.cameras:
                    self.cameras[key] = self.module.create(device_idx=device_idx, output_idx=output_idx,
                        output_color="BGRA", backend="dxgi", processor_backend="numpy", max_buffer_len=2)
                local = left - bounds.left, top - bounds.top, right - bounds.left, bottom - bounds.top
                frame = self.cameras[key].grab(region=local, new_frame_only=True)
                if frame is None or frame.size == 0:
                    return None
                import numpy as np
                height, width = frame.shape[:2]
                ys, xs = np.arange(54) * height // 54, np.arange(96) * width // 96
                return frame[ys[:, None], xs[None, :], :4].tobytes()
        raise RuntimeError("游戏采样区域跨显示器或输出边界暂不可用")

    def read(self, hwnd, roi=(0.12, 0.12, 0.88, 0.70)):
        region = self._region(hwnd, roi)
        if region is None:
            self.backend = "画面不可采集"
            return None
        self._load_dxgi()
        if self.module is not None and time.monotonic() >= self.retry_after:
            try:
                pixels = self._dxgi_read(region)
                if pixels:
                    self.backend, self.detail = "DXGI", ""
                    return pixels
            except Exception as exc:
                self.detail = f"DXGI 重建中：{type(exc).__name__}: {exc}"
                self.close()
                self.retry_after = time.monotonic() + 2
        self.backend = "GDI 回退"
        return self.gdi.read(hwnd, roi)

    def close(self):
        for camera in self.cameras.values():
            try:
                camera.release()
            except Exception:
                pass
        self.cameras.clear()
