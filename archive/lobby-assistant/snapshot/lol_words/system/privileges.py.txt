"""Windows 权限诊断与用户主动提权；只读取进程令牌的提权布尔值。"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import sys


def is_elevated():
    if os.name!='nt':return False
    return bool(C.windll.shell32.IsUserAnAdmin())


def process_elevated(native,pid):
    """不读取进程内存、认证令牌内容或其他账号资料。不可读时返回未知。"""
    handle=native.k.OpenProcess(0x1000,False,pid)
    if not handle:return None
    token=W.HANDLE()
    try:
        api=C.WinDLL('advapi32',use_last_error=True)
        api.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.HANDLE)]
        api.OpenProcessToken.restype=W.BOOL
        api.GetTokenInformation.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)]
        api.GetTokenInformation.restype=W.BOOL
        if not api.OpenProcessToken(handle,0x8,C.byref(token)):return None
        elevated,length=W.DWORD(),W.DWORD()
        if not api.GetTokenInformation(token,20,C.byref(elevated),C.sizeof(elevated),C.byref(length)):return None
        return bool(elevated.value)
    finally:
        if token.value:native.k.CloseHandle(token)
        native.k.CloseHandle(handle)


def restart_elevated(root):
    """仅在用户点击后调用标准 UAC；新窗口保持暂停并只读检查大厅。"""
    root=Path(root).resolve()
    executable=Path(sys.executable).resolve()
    if executable.name.casefold()=='python.exe' and executable.with_name('pythonw.exe').is_file():
        executable=executable.with_name('pythonw.exe')
    entry=root/'launchers/start.pyw'
    if not executable.is_file() or not entry.is_file():raise ValueError('未找到本工具启动文件，请从完整解压目录启动。')
    shell=C.WinDLL('shell32',use_last_error=True)
    shell.ShellExecuteW.argtypes=[W.HWND,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,C.c_int]
    shell.ShellExecuteW.restype=C.c_void_p
    result=shell.ShellExecuteW(None,'runas',str(executable),
        subprocess.list2cmdline([str(entry),'--paused','--lobby']),str(root),1)
    if not result or result<=32:
        if C.get_last_error()==1223:raise ValueError('已取消管理员重启；工具保持暂停，可继续使用或稍后重试。')
        raise ValueError('Windows 未能启动管理员窗口；请右键 start-admin.cmd，以管理员身份运行。')
    return '管理员窗口已启动，将保持暂停并只读检查大厅。'
