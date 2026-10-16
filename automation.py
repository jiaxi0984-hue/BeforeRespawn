"""对局生命周期和 Anki 管理；系统操作通过依赖注入，便于验证。"""
import subprocess
from pathlib import Path


def launch_anki(path):
    executable = Path(path)
    if not executable.is_file():
        raise ValueError("自动查找尚未发现已安装的 Anki")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 4
    return subprocess.Popen([str(executable)], cwd=str(executable.parent), startupinfo=info)


class MatchTracker:
    """接口短暂失联不等于对局结束；GameEnd 或游戏进程消失才结束。"""
    def __init__(self, exit_grace=3.0):
        self.exit_grace = exit_grace
        self.pid = None
        self.active = False
        self.blocked = False
        self.absent_since = None
        self.last_time = None

    def update(self, pid, ready, ended, now, game_time=None):
        actions = []
        if pid is None:
            if self.absent_since is None:
                self.absent_since = now
            if now - self.absent_since >= self.exit_grace:
                if self.active:
                    actions.append("exit")
                self.active = False
                self.pid = self.last_time = None
                self.blocked = False
            return actions
        self.absent_since = None
        reset_time = (ready and game_time is not None and self.last_time is not None
                      and game_time + 5 < self.last_time)
        if self.pid != pid or reset_time:
            if self.active:
                actions.append("exit")
            self.active = False
            self.blocked = False
            self.pid = pid
            self.last_time = None
        if ready and game_time is not None:
            self.last_time = game_time
        if ended:
            self.blocked = True
            if self.active:
                actions.append("exit")
            self.active = False
        elif ready and not self.blocked and not self.active:
            self.active = True
            actions.append("enter")
        return actions


class AnkiManager:
    def __init__(self, windows, report, launch=launch_anki):
        self.windows, self.report, self.launch = windows, report, launch
        self.active = False
        self.hwnd = None
        self.pending = False
        self.attempted = False
        self.launched = False
        self.close_allowed = False
        self.started = 0
        self.warned = False
        self.last_game = None
        self.late_until = 0
        self.closing = set()

    def begin(self, now, preserve_existing=False):
        self.active = True
        self.hwnd = self.windows.find("anki")
        preexisting = bool(self.hwnd or self.windows.anki_running())
        self.close_allowed = not (preexisting and preserve_existing)
        self.attempted = self.pending = self.launched = self.warned = False
        self.started = now
        self.last_game = None
        if self.hwnd:
            self.report("已进入对局，复用现有 Anki 窗口。")
        elif preexisting:
            self.pending = self.attempted = True
            self.report("Anki 正在运行，等待主窗口，不重复启动。")

    def ensure(self, path, now):
        if not self.active or self.hwnd or self.attempted:
            return
        if self.windows.find("anki") or self.windows.anki_running():
            self.pending = self.attempted = True
            self.started = now
        elif path:
            self.attempted = True
            try:
                self.last_game = self.windows.current_game()
                self.launch(path)
                self.pending = self.launched = self.close_allowed = True
                self.started = now
                self.report("检测到进入对局，已自动打开 Anki。")
            except (OSError, ValueError) as exc:
                self.report(f"Anki 自动启动失败：{exc}")

    def poll(self, now):
        for hwnd in list(self.closing):
            if self.windows.kind(hwnd) != "anki":
                self.closing.remove(hwnd)
                self.report("Anki 窗口已关闭。")
        if self.active:
            if self.windows.foreground() != "anki":
                self.last_game = self.windows.current_game()
            if self.pending:
                target = self.windows.find("anki")
                if target:
                    self.hwnd, self.pending = target, False
                    if self.launched and self.last_game and self.windows.foreground() == "anki":
                        self.windows.activate(self.last_game)
                    self.report("Anki 已就绪；死亡灰屏切换已可用。")
                elif not self.warned and now - self.started >= 45:
                    self.warned = True
                    self.report("Anki 窗口仍未就绪；继续监测对局，不重复启动，请查看 Anki 启动提示。")
            if self.hwnd and self.windows.kind(self.hwnd) != "anki":
                self.hwnd = None
                self.attempted = True
                self.report("Anki 已被关闭，本局不重复打开；下一局会再次自动启动。")
        elif now < self.late_until:
            target = self.windows.find("anki")
            if target:
                self.close_window(target)
                self.late_until = 0
        return self.hwnd if self.active else None

    def close_window(self, hwnd):
        if hwnd in self.closing:
            return
        if self.windows.close_anki(hwnd):
            self.closing.add(hwnd)
            self.report("对局结束，已请求 Anki 正常退出；如有同步或确认对话框，请在 Anki 中处理。")
        else:
            self.report("未能正常关闭 Anki，请查看窗口；不会强制结束进程。")

    def end(self, now):
        if not self.active:
            return
        if self.close_allowed:
            target = self.hwnd or self.windows.find("anki")
            if target:
                self.close_window(target)
            elif self.pending:
                # 对局在 Anki 初始化期间结束，也要处理迟到的窗口。
                self.late_until = now + 45
        else:
            self.report("已结束对局，按设置保留原本打开的 Anki。")
        self.active = False
        self.hwnd = None

    def detach(self):
        # 用户暂停工具不是游戏结束，保留 Anki。
        self.active = False
        self.hwnd = None
        self.late_until = 0
