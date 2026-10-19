"""只使用系统进程清单和窗口接口，不读写游戏内存。"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import re
import time

class LastInputInfo(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('dwTime', W.DWORD)]

class ProcessEntry(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", C.c_size_t), ("th32ModuleID", W.DWORD),
                ("cntThreads", W.DWORD), ("th32ParentProcessID", W.DWORD),
                ("pcPriClassBase", W.LONG), ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]


class Windows:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("此版本仅支持 Windows")
        self.u = C.WinDLL("user32", use_last_error=True)
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.u.EnumWindows.argtypes = [self.callback, W.LPARAM]
        self.u.EnumWindows.restype = W.BOOL
        self.u.IsWindowVisible.argtypes = [W.HWND]
        self.u.IsWindowVisible.restype = W.BOOL
        self.u.GetWindowTextLengthW.argtypes = [W.HWND]
        self.u.GetWindowTextLengthW.restype = C.c_int
        self.u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        self.u.GetWindowTextW.restype = C.c_int
        self.u.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM,
                                              W.UINT, W.UINT, C.POINTER(C.c_size_t)]
        self.u.SendMessageTimeoutW.restype = C.c_ssize_t
        self.u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        self.u.GetClassNameW.restype = C.c_int
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.u.GetWindowThreadProcessId.restype = W.DWORD
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetAncestor.argtypes = [W.HWND, W.UINT]
        self.u.GetAncestor.restype = W.HWND
        self.u.GetWindow.argtypes = [W.HWND, W.UINT]
        self.u.GetWindow.restype = W.HWND
        self.u.IsIconic.argtypes = [W.HWND]
        self.u.IsIconic.restype = W.BOOL
        self.u.ShowWindow.argtypes = [W.HWND, C.c_int]
        self.u.SetForegroundWindow.argtypes = [W.HWND]
        self.u.SetForegroundWindow.restype = W.BOOL
        self.u.ShowWindowAsync.argtypes = [W.HWND, C.c_int]
        self.u.ShowWindowAsync.restype = W.BOOL
        self.u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        self.u.SetWindowPos.restype = W.BOOL
        self.u.BringWindowToTop.argtypes = [W.HWND]
        self.u.BringWindowToTop.restype = W.BOOL
        self.u.SetActiveWindow.argtypes = [W.HWND]
        self.u.SetActiveWindow.restype = W.HWND
        self.u.SetFocus.argtypes = [W.HWND]
        self.u.SetFocus.restype = W.HWND
        self.u.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
        self.u.PeekMessageW.restype = W.BOOL
        self._get_style = getattr(self.u, 'GetWindowLongPtrW', self.u.GetWindowLongW)
        self._get_style.argtypes = [W.HWND, C.c_int]
        self._get_style.restype = C.c_ssize_t
        self.u.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
        self.u.AttachThreadInput.restype = W.BOOL
        self.u.GetAsyncKeyState.argtypes = [C.c_int]
        self.u.GetAsyncKeyState.restype = C.c_short
        self.u.GetLastInputInfo.argtypes = [C.POINTER(LastInputInfo)]
        self.u.GetLastInputInfo.restype = W.BOOL
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR,
                                                    C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.k.GetCurrentThreadId.restype = W.DWORD
        self.k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = W.HANDLE
        self.k.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(ProcessEntry)]
        self.k.Process32FirstW.restype = W.BOOL
        self.k.Process32NextW.argtypes = [W.HANDLE, C.POINTER(ProcessEntry)]
        self.k.Process32NextW.restype = W.BOOL

    def processes(self):
        cached_at, cached = getattr(self, '_process_cache', (0, []))
        if time.monotonic() - cached_at < 0.5:
            return cached
        handle = self.k.CreateToolhelp32Snapshot(2, 0)
        if not handle or handle == C.c_void_p(-1).value:
            return cached
        result = []
        try:
            entry = ProcessEntry()
            entry.dwSize = C.sizeof(entry)
            present = self.k.Process32FirstW(handle, C.byref(entry))
            while present:
                result.append((entry.th32ProcessID, entry.szExeFile.casefold()))
                present = self.k.Process32NextW(handle, C.byref(entry))
        finally:
            self.k.CloseHandle(handle)
        self._process_cache = time.monotonic(), result
        return result

    def process_identity(self, pid):
        """完整映像路径加创建时间，防止进程号被复用后误认窗口。"""
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            text, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.k.QueryFullProcessImageNameW(handle, 0, text, C.byref(size)):
                return None
            self.k.GetProcessTimes.argtypes = [W.HANDLE, C.POINTER(W.FILETIME), C.POINTER(W.FILETIME),
                                               C.POINTER(W.FILETIME), C.POINTER(W.FILETIME)]
            self.k.GetProcessTimes.restype = W.BOOL
            created, exited, kernel, user = W.FILETIME(), W.FILETIME(), W.FILETIME(), W.FILETIME()
            if not self.k.GetProcessTimes(handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)):
                return None
            stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return dict(pid=int(pid), path=str(Path(text.value).resolve()), created=stamp)
        finally:
            self.k.CloseHandle(handle)

    def window_inventory(self):
        """只枚举可见主窗口和进程元数据，不读取其内容或浏览历史。"""
        matches, identities = [], {}
        @self.callback
        def visit(hwnd, _):
            if not self.u.IsWindowVisible(hwnd) or self.u.GetWindow(hwnd, 4):
                return True
            pid = W.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if pid.value not in identities:
                identities[pid.value] = self.process_identity(pid.value)
            identity = identities[pid.value]
            if identity:
                cls = C.create_unicode_buffer(256)
                self.u.GetClassNameW(hwnd, cls, len(cls))
                matches.append(dict(hwnd=hwnd, title=self.title(hwnd), window_class=cls.value, **identity))
            return True
        self.u.EnumWindows(visit, 0)
        return matches

    def anki_running(self):
        return bool(self.find('anki') or any(name in {'anki.exe', 'ankiw.exe', 'anki-launcher.exe'}
                                            for _, name in self.processes()))

    def league_pid(self):
        return next((pid for pid, name in self.processes() if name == 'league of legends.exe'), None)

    def client_running(self):
        return any(name in {'leagueclient.exe', 'leagueclientux.exe'} for _, name in self.processes())

    def anki_paths(self):
        results = []
        for pid, name in self.processes():
            if name not in {'anki.exe', 'ankiw.exe', 'anki-launcher.exe'}:
                continue
            handle = self.k.OpenProcess(0x1000, False, pid)
            if not handle:
                continue
            try:
                text = C.create_unicode_buffer(32768)
                size = W.DWORD(len(text))
                if self.k.QueryFullProcessImageNameW(handle, 0, text, C.byref(size)):
                    results.append(text.value)
            finally:
                self.k.CloseHandle(handle)
        return results

    def close_anki(self, hwnd):
        if self.kind(hwnd) != 'anki':
            return False
        self.u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        self.u.PostMessageW.restype = W.BOOL
        return bool(self.u.PostMessageW(hwnd, 0x0010, 0, 0))  # 正常关闭窗口，绝不终止进程。

    def current_game(self):
        hwnd = self.u.GetForegroundWindow()
        return hwnd if self.kind(hwnd) == "game" else None

    def title(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if pid.value == os.getpid():
            # 后台发现线程枚举本工具 Qt 窗口时，限制跨线程消息等待。
            # 主界面正在退出也不能让发现线程永久等待 WM_GETTEXT。
            text = C.create_unicode_buffer(1024)
            result = C.c_size_t()
            if self.u.SendMessageTimeoutW(hwnd, 0x000D, len(text), C.addressof(text), 0x0002, 100, C.byref(result)):
                return text.value
            return ''
        text = C.create_unicode_buffer(self.u.GetWindowTextLengthW(hwnd) + 1)
        self.u.GetWindowTextW(hwnd, text, len(text))
        return text.value

    def process(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if pid.value == os.getpid():
            return ""
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            text = C.create_unicode_buffer(32768)
            size = W.DWORD(len(text))
            if self.k.QueryFullProcessImageNameW(handle, 0, text, C.byref(size)):
                return Path(text.value).name.casefold()
            return ""
        finally:
            self.k.CloseHandle(handle)

    def kind(self, hwnd):
        if not hwnd:
            return "other"
        process = self.process(hwnd)
        if process == "league of legends.exe":
            return "game"
        is_anki_process = process in {"anki.exe", "ankiw.exe", "anki-launcher.exe", "anki"}
        if process in {"python.exe", "pythonw.exe"}:
            class_name = C.create_unicode_buffer(256)
            self.u.GetClassNameW(hwnd, class_name, len(class_name))
            is_anki_process = class_name.value.startswith("Qt")
        # 原生 Anki 进程中的编辑/同步对话框可能没有 Anki 标题，也属于 Anki。
        if is_anki_process and process not in {'python.exe', 'pythonw.exe'}:
            return 'anki'
        owner = self.u.GetAncestor(hwnd, 3)
        if (is_anki_process and (re.search(r"\banki\b", self.title(hwnd), re.I)
                                or re.search(r"\banki\b", self.title(owner), re.I))):
            return "anki"
        return "other"

    def foreground(self):
        return self.kind(self.u.GetForegroundWindow())

    def foreground_handle(self):
        return self.u.GetForegroundWindow()

    def find(self, kind):
        matches = []

        @self.callback
        def visit(hwnd, _):
            # 跳过拥有者窗口的子对话框，优先 Anki 主窗口。
            if (self.u.IsWindowVisible(hwnd) and not self.u.GetWindow(hwnd, 4)
                    and self.kind(hwnd) == kind):
                matches.append(hwnd)
            return True

        self.u.EnumWindows(visit, 0)
        return matches[0] if matches else None

    def activate(self, hwnd):
        if not hwnd:
            return False
        if self.u.IsIconic(hwnd):
            self.u.ShowWindowAsync(hwnd, 9)
        foreground = self.u.GetForegroundWindow()
        current_thread = self.k.GetCurrentThreadId()
        foreground_thread = self.u.GetWindowThreadProcessId(foreground, None)
        target_thread = self.u.GetWindowThreadProcessId(hwnd, None)
        message = W.MSG()
        self.u.PeekMessageW(C.byref(message), None, 0, 0, 0)  # 确保工作线程具有消息队列。
        attached = []
        try:
            for thread in {foreground_thread, target_thread}:
                if thread and thread != current_thread and self.u.AttachThreadInput(current_thread, thread, True):
                    attached.append(thread)
            self.u.BringWindowToTop(hwnd)
            self.u.SetForegroundWindow(hwnd)
            self.u.SetActiveWindow(hwnd)
            self.u.SetFocus(hwnd)
        finally:
            for thread in reversed(attached):
                self.u.AttachThreadInput(current_thread, thread, False)
        actual = self.u.GetForegroundWindow()
        return bool(actual and self.u.GetAncestor(actual, 3) == self.u.GetAncestor(hwnd, 3))

    def is_topmost(self, hwnd):
        return bool(hwnd and self._get_style(hwnd, -20) & 0x00000008)

    def set_topmost(self, hwnd, enabled):
        # 只更改堆叠顺序，不移动或缩放 Anki，也不借此给游戏发送按键。
        after = C.c_void_p(-1 if enabled else -2)
        flags = 0x0001 | 0x0002 | 0x0010
        if self.u.IsWindowVisible(hwnd):
            flags |= 0x0040
        return bool(self.u.SetWindowPos(hwnd, after, 0, 0, 0, 0, flags))

    def is_iconic(self, hwnd):
        return bool(hwnd and self.u.IsIconic(hwnd))

    def is_visible(self, hwnd):
        return bool(hwnd and self.u.IsWindowVisible(hwnd))

    def minimize(self, hwnd):
        return bool(self.u.ShowWindowAsync(hwnd, 6))

    def restore(self, hwnd, activate=True):
        if not activate:
            self.u.ShowWindowAsync(hwnd, 4)
            return True
        if self.is_iconic(hwnd):
            # 全屏窗口恢复是异步的，不在它仍最小化时反复争抢输入焦点。
            requests = getattr(self, '_restore_requests', {})
            now = time.monotonic()
            if now - requests.get(hwnd, -1e9) >= 0.5:
                self.u.ShowWindowAsync(hwnd, 9)
                requests[hwnd] = now
                self._restore_requests = requests
            if self.is_iconic(hwnd):
                return False
        return self.activate(hwnd)

    def input_serial(self):
        # 只读取系统最后输入的时间戳，不读取按键或鼠标内容。
        info = LastInputInfo()
        info.cbSize = C.sizeof(info)
        return info.dwTime if self.u.GetLastInputInfo(C.byref(info)) else None

    def desktop_foreground(self):
        hwnd = self.u.GetForegroundWindow()
        if not hwnd:
            return True
        name = C.create_unicode_buffer(256)
        self.u.GetClassNameW(hwnd, name, len(name))
        return name.value in {'Progman', 'WorkerW', 'Shell_TrayWnd'}

    def focus_diagnostics(self, hwnd):
        front = self.u.GetForegroundWindow()
        return (f"前台进程={self.process(front) or '系统桌面'}，"
                f"目标置顶={self.is_topmost(hwnd)}，目标最小化={self.is_iconic(hwnd)}，"
                f"系统错误={C.get_last_error()}")

    def stop_pressed(self):
        # 只检测固定暂停组合键，不收集或保存任何按键内容。
        return all(self.u.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x77))


