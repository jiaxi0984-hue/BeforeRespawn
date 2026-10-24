"""Windows 用户主动管理员启动；不读取其他进程的连接或认证参数。"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import sys


def is_elevated():
    if os.name!='nt':return False
    return bool(C.windll.shell32.IsUserAnAdmin())


def restart_elevated(root):
    """仅在用户点击后调用标准 UAC；新窗口保持暂停。"""
    root=Path(root).resolve()
    executable=Path(sys.executable).resolve()
    frozen=getattr(sys,'frozen',False)
    if not frozen and executable.name.casefold()=='python.exe' and executable.with_name('pythonw.exe').is_file():
        executable=executable.with_name('pythonw.exe')
    entry=root/'launchers/start.pyw'
    if not executable.is_file() or (not frozen and not entry.is_file()):raise ValueError('未找到本工具启动文件，请从完整解压目录启动。')
    shell=C.WinDLL('shell32',use_last_error=True)
    shell.ShellExecuteW.argtypes=[W.HWND,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,W.LPCWSTR,C.c_int]
    shell.ShellExecuteW.restype=C.c_void_p
    result=shell.ShellExecuteW(None,'runas',str(executable),
        subprocess.list2cmdline(['--paused'] if frozen else [str(entry),'--paused']),str(root),1)
    if not result or result<=32:
        if C.get_last_error()==1223:raise ValueError('已取消管理员重启；工具保持暂停，可继续使用或稍后重试。')
        raise ValueError('Windows 未能启动管理员窗口；请右键 start-admin.cmd，以管理员身份运行。')
    return '管理员窗口已启动，保持暂停；请点击“继续监测”开始。'
