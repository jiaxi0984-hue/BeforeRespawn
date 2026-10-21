"""泉水背词：自动发现 Anki，按英雄联盟实际对局管理其生命周期。"""
from __future__ import annotations
import argparse
import ctypes as C
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from automation import AnkiManager, MatchTracker
from capture import SceneCapture
from core import DeathSession, GrayGate, frame_stats
from discovery import locate_anki
from game_api import GameData, NoRedirect
from native import Windows
from presentation import StudyWindow

ROOT = Path(__file__).resolve().parent
DEFAULTS = dict(anki_path="", player_id="", return_before_seconds=3.0, poll_seconds=0.2,
                gray_threshold=0.82, confirmation_frames=2, capture_region=[0.12, 0.12, 0.88, 0.70],
                preserve_existing_anki=False, game_display_mode="auto")
DISPLAY_MODES = {"自动兼容（全屏 / 无边框）": "auto", "无边框": "borderless", "全屏": "fullscreen"}


class App:
    def __init__(self, root, autostart=True):
        self.root, self.windows = root, Windows()
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = self.finder = None
        self.closing = self.hotkey_down = False
        self._test_active = False
        self.pin_tester = None
        self._presentation = None
        self.config = DEFAULTS.copy()
        try:
            settings = ROOT / "config.json"
            if not settings.exists():
                settings = ROOT / "config.example.json"
            saved = json.loads(settings.read_text(encoding="utf-8-sig"))
            self.config.update({key: saved[key] for key in DEFAULTS if key in saved})
        except (OSError, ValueError, TypeError):
            pass
        cached = self.config["anki_path"]
        self.detected_path = cached if cached and Path(cached).is_file() else ""
        self.path_label = tk.StringVar(value=self.detected_path or "启动后自动查找，无需手动填写")
        self.lead = tk.StringVar(value=str(self.config["return_before_seconds"]))
        self.threshold = tk.StringVar(value=str(round(self.config["gray_threshold"] * 100)))
        self.preserve = tk.BooleanVar(value=self.config["preserve_existing_anki"])
        self.display_mode = tk.StringVar(value=next((label for label, value in DISPLAY_MODES.items()
                                            if value == self.config["game_display_mode"]), next(iter(DISPLAY_MODES))))
        self.status = tk.StringVar(value="准备自动监测")
        self.build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.tick)
        if autostart:
            root.after(350, self.start)

    def build(self):
        self.root.title("泉水背词 · 无边框返回修正版")
        self.root.geometry("900x640")
        self.root.minsize(850, 600)
        style = ttk.Style()
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=6)
        box = ttk.Frame(self.root, padding=22)
        box.pack(fill="both", expand=True)
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text="泉水背词 · 自动模式", font=("Microsoft YaHei UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(box, text="等待游戏 → 进入对局自动打开 Anki → 死亡灰屏切换 → 对局结束正常关闭", wraplength=740).grid(row=1, column=0, sticky="w", pady=(8, 16))
        ttk.Label(box, text="Anki 自动查找结果").grid(row=2, column=0, sticky="w")
        ttk.Label(box, textvariable=self.path_label, foreground="#155e75", wraplength=740).grid(row=3, column=0, sticky="w", pady=(5, 12))
        options = ttk.Frame(box)
        options.grid(row=4, column=0, sticky="w", pady=8)
        ttk.Label(options, text="灰色占比至少").pack(side="left")
        ttk.Spinbox(options, from_=50, to=99, width=5, textvariable=self.threshold).pack(side="left", padx=6)
        ttk.Label(options, text="%；复活前").pack(side="left")
        ttk.Spinbox(options, from_=0, to=30, width=5, textvariable=self.lead).pack(side="left", padx=6)
        ttk.Label(options, text="秒返回").pack(side="left")
        ttk.Label(options, text="  游戏显示：").pack(side="left")
        mode_box = ttk.Combobox(options, textvariable=self.display_mode, values=list(DISPLAY_MODES),
                                state="readonly", width=25)
        mode_box.pack(side="left")
        mode_box.bind('<<ComboboxSelected>>', lambda _: self.save())
        ttk.Checkbutton(box, text="保留对局开始前已经打开的 Anki（未勾选时，对局结束也会正常关闭它）", variable=self.preserve).grid(row=5, column=0, sticky="w", pady=6)
        buttons = ttk.Frame(box)
        buttons.grid(row=6, column=0, sticky="w", pady=10)
        for label, command in (("暂停", self.stop), ("恢复自动监测", self.start), ("重新自动查找", lambda: self.rediscover(force=True)), ("保存设置", self.save), ("测试往返（3秒）", self.test_pin)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 8))
        ttk.Label(box, textvariable=self.status, wraplength=740, foreground="#155e75").grid(row=7, column=0, sticky="w", pady=8)
        ttk.Label(box, text="死亡灰屏后临时置顶 Anki；返回、暂停或手动切走时恢复置顶状态。\n自动兼容 / 全屏模式会先最小化游戏，复活时恢复；无需更改游戏画质或显示设置。", wraplength=840).grid(row=8, column=0, sticky="w", pady=(0, 10))
        self.log = tk.Text(box, state="disabled", height=9, wrap="word", relief="flat", background="#f1f5f9", font=("Microsoft YaHei UI", 9))
        self.log.grid(row=9, column=0, sticky="nsew")
        box.rowconfigure(9, weight=1)
        self.append("自动监测区分大厅、加载、对局中和结束；等待游戏期间不会打开 Anki。")

    def append(self, text):
        line = time.strftime("%H:%M:%S ") + text + "\n"
        self.log.configure(state="normal")
        self.log.insert("end", line)
        if int(self.log.index("end-1c").split(".")[0]) > 180:
            self.log.delete("1.0", "40.0")
        self.log.see("end")
        self.log.configure(state="disabled")
        try:
            path = ROOT / "runtime.log"
            if path.exists() and path.stat().st_size > 300000:
                path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8") as out:
                out.write(line)
        except OSError:
            pass

    def save(self, report=True):
        try:
            lead, threshold = float(self.lead.get()), float(self.threshold.get()) / 100
            if not (0 <= lead <= 30 and 0.5 <= threshold <= 0.99):
                raise ValueError
            self.config.update(anki_path=self.detected_path, return_before_seconds=lead, gray_threshold=threshold, preserve_existing_anki=self.preserve.get())
            self.config["game_display_mode"] = DISPLAY_MODES[self.display_mode.get()]
            temporary = ROOT / "config.tmp"
            temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(ROOT / "config.json")
        except ValueError:
            messagebox.showerror("设置无效", "灰色占比须为 50–99%，提前返回为 0–30 秒。")
            return False
        except OSError as exc:
            self.append(f"设置未能保存：{exc}")
            return False
        if report:
            self.append("设置已保存；显示方式立即生效，其余参数暂停并恢复后生效。")
        return True

    def current_display_mode(self, fallback):
        mode = getattr(self, 'config', {}).get('game_display_mode', fallback)
        return mode if mode in DISPLAY_MODES.values() else fallback

    def rediscover(self, force=False):
        if self.finder and self.finder.is_alive():
            return
        cached = "" if force else self.detected_path
        def find():
            self.events.put(("log", "正在自动查找 Anki 的安装位置…"))
            try:
                path = locate_anki(cached, ROOT, self.windows.anki_paths())
                if path:
                    self.detected_path = path
                    self.events.put(("path", path))
                    self.events.put(("log", f"已自动找到 Anki：{path}"))
                else:
                    self.events.put(("log", "尚未找到 Anki，会继续自动查找；游戏监测保持运行。"))
            except Exception as exc:
                self.events.put(("log", f"自动查找暂未完成：{type(exc).__name__}: {exc}"))
        self.finder = threading.Thread(target=find, daemon=True)
        self.finder.start()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.save(report=False):
            return
        self.stop_event = threading.Event()
        self.rediscover()
        self.worker = threading.Thread(target=self.monitor, args=(dict(self.config), self.stop_event), daemon=True)
        self.worker.start()
        self.append("自动监测已启动，等待英雄联盟；无需每局手动开始。")

    def return_game(self):
        presentation = getattr(self, '_presentation', None)
        if presentation and presentation.owned:
            if presentation.returning:
                return
            result = presentation.request_return(time.monotonic())
            if result == 'pending':
                self.events.put(("log", "已取消 Anki 临时置顶并让出前台，正在等待游戏恢复；保留返回记录继续重试。"))
            self.log_return_result(presentation, result)
            return
        if self.windows.foreground() != "anki":
            return
        target = self.windows.find("game")
        if target and self.windows.activate(target):
            self.events.put(("log", "已切回游戏。"))
        elif target:
            self.events.put(("log", "自动返回失败，请手动切回游戏。"))
            self.events.put(("bell", ""))

    def log_return_result(self, presentation, result):
        if result == 'returned':
            self.events.put(("log", "返回游戏成功：" + presentation.last_return_reason))
        elif result == 'failed':
            self.events.put(("log", "返回游戏失败：" + presentation.last_return_reason + "；请手动切回。"))
            self.events.put(("bell", ""))
        elif result == 'cancelled':
            self.events.put(("log", "已停止返回：" + presentation.last_return_reason))

    def finish_return(self, presentation):
        """用于暂停或往返测试收尾，最多等待约 3 秒，界面线程不阻塞。"""
        result = presentation.request_return(time.monotonic())
        self.log_return_result(presentation, result)
        for _ in range(60):
            if not presentation.returning:
                break
            time.sleep(0.05)
            self.log_return_result(presentation, presentation.poll_return(time.monotonic()))
        if presentation.owned:
            presentation.release(activate=False)

    def test_pin(self):
        current = getattr(self, '_presentation', None)
        if current and current.returning:
            self.events.put(("log", "游戏正在恢复中，请等待本次返回完成再测试往返。"))
            return
        if self.pin_tester and self.pin_tester.is_alive():
            return
        target, game = self.windows.find("anki"), self.windows.find("game")
        if not target:
            messagebox.showinfo("Anki 未打开", "请在进入对局、Anki 自动打开后点测试；此测试不需要等待死亡。")
            return
        mode = DISPLAY_MODES[self.display_mode.get()]
        self._test_active = True
        def run():
            presentation = StudyWindow(self.windows)
            try:
                success = presentation.show(target, game, mode, manual=True)
                self.events.put(("log", "置顶测试：" + presentation.last_reason))
                if success:
                    # 测试最多 3 秒，退出时也必须取消临时置顶。
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline and not self.closing:
                        time.sleep(0.05)
            finally:
                self.finish_return(presentation)
                self._test_active = False
                self.events.put(("log", "往返测试结束；返回结果见上方日志。"))
        self.pin_tester = threading.Thread(target=run, daemon=True)
        self.pin_tester.start()

    def monitor(self, config, stop_event):
        report = lambda text: self.events.put(("log", text))
        tracker, manager = MatchTracker(), AnkiManager(self.windows, report)
        self._presentation = StudyWindow(self.windows)
        session = DeathSession(config["return_before_seconds"])
        gate = GrayGate(config["gray_threshold"], config["confirmation_frames"])
        source, source_pid, next_discovery = GameData(), None, time.monotonic() + 30
        last_error = ""
        next_death_log, last_backend = 0, ""
        last_observation = 'idle'
        capture = None
        try:
            capture = SceneCapture()
            while not stop_event.is_set():
                now = time.monotonic()
                # 恢复窗口不再依赖某一帧死亡状态，游戏接口超时期间也持续处理。
                self.log_return_result(self._presentation, self._presentation.poll_return(now))
                pid = self.windows.league_pid()
                if pid != source_pid and pid is not None:
                    source, source_pid = GameData(), pid
                ready, ended, game_time = False, False, None
                if pid is not None:
                    try:
                        game_time, ended = source.status()
                        ready = True
                    except Exception as exc:
                        message = f"等待对局接口：{type(exc).__name__}: {exc}"
                        if message != last_error:
                            report(message)
                            last_error = message
                for action in tracker.update(pid, ready, ended, now, game_time):
                    if action == "exit":
                        if session.engaged or self._presentation.owned:
                            if pid:
                                self.return_game()
                            else:
                                self._presentation.release(activate=False)
                        manager.end(now)
                        report("对局已结束，继续自动等待下一局。")
                    else:
                        report("已确认进入实际对局。")
                        manager.begin(now, config["preserve_existing_anki"])
                        last_error = ""
                    session = DeathSession(config["return_before_seconds"])
                    gate = GrayGate(config["gray_threshold"], config["confirmation_frames"])
                manager.ensure(self.detected_path, now)
                target = manager.poll(now)
                if not self.detected_path and now >= next_discovery:
                    self.rediscover()
                    next_discovery = now + 30
                if tracker.active and ready:
                    try:
                        name, sample = source.read(config.get("player_id", ""))
                        if stop_event.is_set():
                            break
                        if getattr(self, '_test_active', False):
                            stop_event.wait(config["poll_seconds"])
                            continue
                        foreground = self.windows.foreground()
                        game_hwnd = self.windows.current_game() if foreground == "game" else None
                        pixels = capture.read(game_hwnd, config["capture_region"]) if game_hwnd else None
                        stats = frame_stats(pixels) if pixels and self.windows.current_game() == game_hwnd else None
                        backend = getattr(capture, 'backend', '测试采样')
                        if game_hwnd and backend != last_backend:
                            report("画面采样：" + backend + "；" + getattr(capture, 'detail', ''))
                            last_backend = backend
                        gray = gate.observe(sample.dead, stats)
                        observation = self._presentation.observe_foreground(now)
                        if observation == 'game_under_anki' and observation != last_observation:
                            report('游戏焦点发生回跳，但 Anki 仍可见；保留本次复活返回记录，不视为主动返回。')
                        last_observation = observation
                        if self._presentation.owned and (not sample.dead or
                                (sample.timer > 0 and sample.timer <= config["return_before_seconds"])):
                            self.return_game()
                        action = session.update(sample, foreground, gray and bool(target),
                                                manual_return_confirmed=observation != 'game_under_anki')
                        if sample.dead and now >= next_death_log:
                            report(f"死亡检测：剩余={sample.timer:.1f}s；{gate.label}；"
                                   f"触发={gray}；Anki就绪={bool(target)}；前台={foreground}；"
                                   f"本次结束={session.finished}；已尝试={session.attempted}；采样={backend}")
                            next_death_log = now + 3
                        elif not sample.dead:
                            next_death_log = 0
                        if action == "study":
                            success = (not stop_event.is_set() and self.windows.foreground() == "game"
                                       and target is not None and self._presentation.show(target, game_hwnd,
                                                                                       self.current_display_mode(config["game_display_mode"])))
                            session.confirm_study(success)
                            report("死亡黑白画面已确认：" + self._presentation.last_reason)
                        elif action == "return":
                            self.return_game()
                        elif action == "manual_return":
                            if not self._presentation.returning:
                                self._presentation.release(activate=False)
                            report("已主动返回游戏，本次死亡不再切到 Anki。")
                        state = f"死亡 · 复活剩余 {sample.timer:.1f} 秒" if sample.dead else "存活"
                        if foreground == "game" and not session.engaged:
                            state += " · " + gate.label
                        if not target:
                            state += " · Anki 准备中"
                        if self._presentation.returning:
                            state += " · 正在恢复游戏前台"
                        self.events.put(("status", f"对局中 · {name} · {state}"))
                        last_error = ""
                    except Exception as exc:
                        if session.engaged or self._presentation.owned:
                            self.return_game()
                            session.confirm_study(False)
                        # 保留已经采集的彩色参照与本次死亡状态，短暂超时不丢弃整次触发。
                        gate.consecutive = 0
                        message = f"对局中，暂时无法读取本人状态：{type(exc).__name__}: {exc}"
                        if message != last_error:
                            report(message)
                            last_error = message
                        self.events.put(("status", "对局中 · 等待本人状态，灰屏切换暂停"))
                elif tracker.active:
                    if session.engaged or self._presentation.owned:
                        self.return_game()
                        session.confirm_study(False)
                    gate.consecutive = 0
                    self.events.put(("status", "对局仍在运行 · 数据暂不可用，保留 Anki 并等待恢复"))
                else:
                    state = ("本局已结束，等待下一局" if pid and tracker.blocked else "游戏加载中，等待进入实际对局" if pid else "英雄联盟大厅中，等待进入对局" if self.windows.client_running() else "等待英雄联盟启动 · 自动监测运行中")
                    self.events.put(("status", state))
                stop_event.wait(config["poll_seconds"] if pid or tracker.active else 0.6)
        except Exception as exc:
            report(f"自动监测发生异常：{type(exc).__name__}: {exc}；可点恢复重新运行。")
        finally:
            if session.engaged or self._presentation.owned:
                self.finish_return(self._presentation)
            if hasattr(capture, 'close'):
                capture.close()
            manager.detach()
            self.events.put(("status", "已暂停；Anki 保留，点恢复继续等待游戏"))

    def stop(self):
        self.stop_event.set()
        self.status.set("正在暂停…")

    def tick(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append(value)
            elif kind == "path":
                self.path_label.set(value)
                self.config["anki_path"] = value
                self.save(report=False)
            elif kind == "status":
                self.status.set(value)
            elif kind == "bell":
                self.root.bell()
        pressed = self.windows.stop_pressed()
        if pressed and not self.hotkey_down:
            self.stop()
        self.hotkey_down = pressed
        if (self.closing and not (self.worker and self.worker.is_alive())
                and not (self.pin_tester and self.pin_tester.is_alive())):
            self.root.destroy()
            return
        self.root.after(100, self.tick)

    def close(self):
        self.closing = True
        self.stop_event.set()
        self.status.set("正在退出…")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    if args.diagnose:
        windows = Windows()
        print(json.dumps(dict(anki=locate_anki(project_root=ROOT, running_paths=windows.anki_paths()), league_pid=windows.league_pid(), client_running=windows.client_running()), ensure_ascii=False))
        return
    C.WinDLL("user32").SetProcessDPIAware()
    root = tk.Tk()
    App(root, autostart=not args.check)
    if args.check:
        root.update()
        capture = SceneCapture()
        pixels = capture.read(root.winfo_id())
        if not pixels:
            raise RuntimeError("本机画面采样检查失败")
        print(f"中文界面和系统采样正常（{len(pixels)} 字节）；后端={capture.backend}；{capture.detail}；检查模式未启动监测、Anki 或关闭应用。")
        capture.close()
        root.destroy()
    else:
        root.mainloop()


if __name__ == "__main__":
    main()
