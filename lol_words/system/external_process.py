"""冻结程序启动外部软件时不继承内置 DLL 目录；不改变全局 PATH。"""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading

_spawn_lock = threading.RLock()


def external_environment(environment=None):
    env = dict(os.environ if environment is None else environment)
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        root = Path(bundle).resolve()
        for key in ('PATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
            if key in env:
                env[key] = os.pathsep.join(value for value in env[key].split(os.pathsep)
                    if value and not Path(value).resolve().is_relative_to(root))
    return env


@contextmanager
def external_dll_directory():
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        yield
        return
    # Windows exposes process-global state. Serialize every external spawn, restore
    # immediately after CreateProcess, and never hold this state while waiting.
    with _spawn_lock:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        kernel.GetDllDirectoryW.restype = ctypes.c_uint
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetDllDirectoryW(len(buffer), buffer)
        if length >= len(buffer):
            raise OSError('DLL 搜索目录过长，已取消启动外部程序')
        previous = buffer.value if length else None
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous):
                raise ctypes.WinError(ctypes.get_last_error())


def popen_external(*args, **kwargs):
    if getattr(sys, 'frozen', False):
        kwargs['env'] = external_environment(kwargs.get('env'))
    with external_dll_directory():
        return subprocess.Popen(*args, **kwargs)


def run_external(command, *, capture_output=False, timeout=None, **kwargs):
    """Only the subset used by read-only discovery; wait outside the DLL lock."""
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with popen_external(command, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
