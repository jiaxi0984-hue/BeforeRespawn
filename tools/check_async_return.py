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
from lol_words.system.native import Windows
from lol_words.system.presentation import StudyWindow

w = Windows()
if w.league_pid():
    print('SKIP: 实际对局正在运行，未执行窗口测试。')
    raise SystemExit(0)
folder = Path(__file__).parent / ('native-return-' + uuid.uuid4().hex[:8])
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
    if sys.argv[2]=='game' and request.exists():
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
    w.activate(handles['game'])
    w.minimize(handles['game'])
    time.sleep(0.25)
    w.activate(handles['anki'])
    time.sleep(0.12)
    presentation = StudyWindow(w, target_kind="anki")
    if not presentation.show(handles['anki'], handles['game'], 'borderless', manual=True):
        raise RuntimeError('未能切到 Anki 测试窗口：' + presentation.last_reason)
    (folder / 'block').write_text('block', encoding='utf-8')
    deadline = time.monotonic() + 2
    while not (folder / 'blocked').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not (folder / 'blocked').exists():
        raise RuntimeError('异步延迟场景未建立')
    started = time.monotonic()
    result = presentation.request_return(started)
    if result != 'pending' or not presentation.owned:
        raise RuntimeError(f'游戏窗口尚未恢复时没有保留返回任务：{result}')
    while result == 'pending':
        time.sleep(0.04)
        result = presentation.poll_return(time.monotonic())
    if result != 'returned':
        raise RuntimeError('跨进程异步返回失败：' + presentation.last_return_reason)
    if w.current_game() != handles['game'] or w.is_iconic(handles['game']):
        raise RuntimeError('没有验证到真实游戏替身窗口的前台状态')
    if w.is_topmost(handles['anki']):
        raise RuntimeError('Anki 测试窗口残留置顶')
    print('PASS: 两个独立进程；游戏替身延迟0.8秒处理恢复；返回任务保留并最终取得真实前台。')
    print(presentation.last_return_reason)
    print('PASS: Anki替身已取消置顶并最小化；未操作真实Anki或英雄联盟。')
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
