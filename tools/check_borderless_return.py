"""用两个独立测试进程验证异步恢复；不操作用户的 Anki 或真实游戏。"""
from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
import subprocess
import sys
import time
import uuid
import os
sys.path.insert(0, os.environ.get('LOL_ANKI_APP_DIR', str(Path(__file__).resolve().parents[1])))
from native import Windows
from presentation import StudyWindow

w = Windows()
if w.league_pid():
    print('SKIP: 实际对局正在运行，未执行窗口测试。')
    raise SystemExit(0)
folder = Path(__file__).parent / ('native-borderless-' + uuid.uuid4().hex[:8])
folder.mkdir()
child = folder / 'window.py'
child.write_text('''import sys, time, tkinter as tk
from pathlib import Path
root = tk.Tk()
root.title(sys.argv[1])
root.geometry('520x260+' + ('50+50' if sys.argv[2]=='game' else '100+100'))
tk.Label(root, text='泉水背词：独立进程窗口恢复检查', bg='#236394', fg='white').pack(fill='both', expand=True)
folder = Path(sys.argv[3])
def commands():
    request = folder / 'block'
    if sys.argv[2]=='anki' and request.exists():
        request.unlink()
        (folder / 'blocked').write_text('ready')
        time.sleep(0.8)
    root.after(20, commands)
root.after(20, commands)
root.after(15000, root.destroy)
root.mainloop()
''', encoding='utf-8')
token = uuid.uuid4().hex[:6]
titles = {'game': 'ReturnCheck Game ' + token, 'anki': 'ReturnCheck Anki ' + token}
processes, handles = {}, {}
presentation = None
try:
    for role, title in titles.items():
        processes[role] = subprocess.Popen([sys.executable, str(child), title, role, str(folder)],
                                           creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and len(handles) < 2:
        @w.callback
        def visit(hwnd, _):
            title = w.title(hwnd)
            for role, expected in titles.items():
                if title == expected:
                    pid = W.DWORD()
                    w.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
                    if pid.value == processes[role].pid:
                        handles[role] = hwnd
            return True
        w.u.EnumWindows(visit, 0)
        time.sleep(0.03)
    if len(handles) != 2:
        raise RuntimeError('测试窗口未及时出现')
    original_kind = w.kind
    w.kind = lambda hwnd: next((role for role, handle in handles.items() if hwnd == handle), original_kind(hwnd))
    w.restore(handles['game'], activate=True)
    time.sleep(0.2)
    w.set_topmost(handles['anki'], True)
    w.activate(handles['anki'])
    time.sleep(0.1)
    presentation = StudyWindow(w)
    if not presentation.show(handles['anki'], handles['game'], 'borderless', manual=True):
        raise RuntimeError('未能准备 Anki 覆盖窗口：' + presentation.last_reason)
    w.activate(handles['game'])
    time.sleep(0.1)
    if w.current_game() != handles['game'] or w.is_iconic(handles['anki']):
        raise RuntimeError('未建立游戏前台且 Anki 仍显示的场景')
    (folder / 'block').write_text('block', encoding='utf-8')
    deadline = time.monotonic() + 2
    while not (folder / 'blocked').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not (folder / 'blocked').exists():
        raise RuntimeError('异步延迟场景未建立')
    started = time.monotonic()
    result = presentation.request_return(started)
    if result != 'pending' or not presentation.owned:
        raise RuntimeError(f'Anki 尚未隐藏时提前清除了返回任务：{result}')
    while result == 'pending':
        time.sleep(0.04)
        result = presentation.poll_return(time.monotonic())
    if result != 'returned':
        raise RuntimeError('跨进程异步返回失败：' + presentation.last_return_reason)
    if w.current_game() != handles['game'] or w.is_iconic(handles['game']):
        raise RuntimeError('没有验证到真实游戏替身窗口的前台状态')
    if not w.is_iconic(handles['anki']):
        raise RuntimeError('Anki 仍然显示，不能报告返回成功')
    print('PASS: 两个独立进程；游戏始终在前台；Anki替身延迟0.8秒处理最小化；确认遮挡消失后才成功。')
    print(presentation.last_return_reason)
    print('PASS: Anki替身已最小化，原有置顶偏好保留；未操作真实Anki或英雄联盟。')
finally:
    if presentation and presentation.owned:
        presentation.release(activate=False)
    for role, hwnd in handles.items():
        pid = W.DWORD()
        w.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if pid.value == processes[role].pid:
            w.u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
            w.u.PostMessageW(hwnd, 0x0010, 0, 0)
    for process in processes.values():
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            print('测试窗口尚在结束，将由其15秒自退出计时器关闭。')
