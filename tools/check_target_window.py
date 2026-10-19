"""两个临时进程验证统一桌面适配器，不操作真实游戏、学习软件或网页。"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lol_words.targets.adapters import DesktopAdapter, TargetWindows
from lol_words.system.native import Windows
from lol_words.system.presentation import StudyWindow


def main():
    native=Windows()
    if native.league_pid():
        print('SKIP: 对局正在运行，未进行窗口切换检查');return
    root=Path(__file__).resolve().parents[1]
    area=root/'runtime'/'window-check';area.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(dir=area) as folder:
        child=Path(folder)/'window.py'
        child.write_text("import tkinter as tk,sys\nr=tk.Tk();r.title(sys.argv[1]);r.geometry('480x240+80+80');tk.Label(r,text='临时窗口适配检查').pack();r.after(15000,r.destroy);r.mainloop()",encoding='utf-8')
        titles={'game':'Game fixture '+token,'target':'Study fixture '+token}
        processes={role:subprocess.Popen([sys.executable,str(child),title],creationflags=subprocess.CREATE_NO_WINDOW) for role,title in titles.items()}
        handles={};presentation=None
        try:
            deadline=time.monotonic()+4
            while time.monotonic()<deadline and len(handles)<2:
                for row in native.window_inventory():
                    for role,title in titles.items():
                        if row['title']==title and row['pid']==processes[role].pid:handles[role]=row['hwnd']
                time.sleep(.05)
            if len(handles)!=2:raise RuntimeError('临时窗口未就绪')
            original=native.kind
            native.kind=lambda hwnd:'game' if hwnd==handles['game'] else original(hwnd)
            spec=dict(id='fixture',name='学习替身',adapter='desktop',command=[sys.executable,str(child),titles['target']],
                      process_path=sys.executable,title_contains=titles['target'],window_class='',pin=True)
            adapter=DesktopAdapter(spec,native,root);context=TargetWindows(native,adapter)
            if adapter.find()!=handles['target']:raise RuntimeError('未唯一匹配桌面目标窗口')
            presentation=StudyWindow(context)
            if not presentation.show(adapter.find(),handles['game'],'borderless'):raise RuntimeError(presentation.last_reason)
            native.activate(handles['game']);presentation.observe_foreground(time.monotonic())
            if not native.is_topmost(handles['target']):raise RuntimeError('目标未保持置顶')
            result=presentation.request_return(time.monotonic())
            while result=='pending':time.sleep(.05);result=presentation.poll_return(time.monotonic())
            if result!='returned':raise RuntimeError(presentation.last_return_reason)
            print('PASS: 完整进程路径 + 唯一标题匹配、置顶和复活返回替身检查通过')
            print('NOT A GAME TEST: 未验证真实英雄联盟或第三方学习软件')
        finally:
            if presentation:presentation.release(activate=False)
            for role,handle in handles.items():
                identity=native.process_identity(processes[role].pid)
                if identity:
                    native.u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
                    native.u.PostMessageW(handle,0x0010,0,0)
            for process in processes.values():
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:print('临时窗口将由 15 秒自退出计时器关闭')


if __name__=='__main__':main()
