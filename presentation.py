"""临时置顶 Anki，并在恢复游戏、用户切走或暂停时恢复窗口状态。"""
import time


class StudyWindow:
    def __init__(self, windows):
        self.windows = windows
        self.anki = self.game = None
        self.original_topmost = False
        self.minimized_game = False
        self.last_reason = ""
        self._return = None
        self.last_return_reason = ""
        self.suspended = False
        self.focus_retries = 0
        self.next_focus_retry = 0

    @property
    def returning(self):
        return self._return is not None

    def _game_ready(self):
        target = self.windows.current_game()
        return bool(target and self.windows.kind(target) == 'game'
                    and not self.windows.is_iconic(target))

    def _anki_cleared(self):
        w = self.windows
        return (not self.anki or w.kind(self.anki) != 'anki' or w.is_iconic(self.anki)
                or not w.is_visible(self.anki))

    def observe_foreground(self, now):
        """窗口焦点不等同于画面遮挡；无边框下保留返回记录。"""
        if not self.owned or self.returning:
            return 'idle'
        w = self.windows
        if self._anki_cleared():
            self.release(activate=False)
            return 'anki_hidden'
        front = w.foreground()
        if front == 'game':
            # Anki 仍可见，不能仅凭游戏短暂取得焦点认定用户完成了返回。
            if (not self.suspended and w.is_topmost(self.anki)
                    and self.focus_retries < 3 and now >= self.next_focus_retry):
                self.focus_retries += 1
                self.next_focus_retry = now + 0.4
                w.activate(self.anki)
            return 'game_under_anki'
        if front == 'anki':
            if self.suspended:
                w.set_topmost(self.anki, True)
                self.suspended = False
            return 'anki'
        # 暂时切去其他应用只取消覆盖，不丢弃稍后返回所需的记录。
        if not self.suspended:
            w.set_topmost(self.anki, self.original_topmost)
            self.suspended = True
        return 'other'

    def _hide_anki(self, now):
        if self._anki_cleared() or now < self._return['next_hide']:
            return
        self._return['next_hide'] = now + 0.4
        if self.windows.minimize(self.anki):
            self._return['minimized_anki'] = True

    def request_return(self, now):
        """发送恢复请求后保留窗口记录，直到真正取得前台或超时。"""
        if self.returning:
            return 'pending'
        if not self.owned:
            return 'idle'
        w = self.windows
        if (w.kind(self.anki) == 'anki'
                and w.is_topmost(self.anki) != self.original_topmost):
            w.set_topmost(self.anki, self.original_topmost)
        if w.foreground() not in {'anki', 'game'} and not w.desktop_foreground():
            self.release(activate=False)
            self.last_return_reason = '用户已切到其他应用，取消自动返回'
            return 'cancelled'
        serial = w.input_serial() if hasattr(w, 'input_serial') else None
        self._return = dict(started=now, deadline=now + 3.0, next_try=now,
                            attempts=0, input_serial=serial, minimized_anki=False, next_hide=now,
                            other_window=None, other_serial=None)
        # 对称处理：先让 Anki 让出前台，而不是仅取消置顶后仍留在游戏前面。
        self._hide_anki(now)
        return self.poll_return(now)

    def _finish_return(self, result, reason):
        w, request = self.windows, self._return
        if result != 'returned' and request and request['minimized_anki']:
            if self.anki and w.kind(self.anki) == 'anki':
                w.restore(self.anki, activate=False)
        self.last_return_reason = reason
        self._return = None
        self.anki = self.game = None
        self.minimized_game = False
        return result

    def poll_return(self, now):
        if not self.returning:
            return 'idle'
        w, request = self.windows, self._return
        self._hide_anki(now)
        if self._game_ready() and self._anki_cleared():
            return self._finish_return('returned',
                f"已确认游戏取得前台且 Anki 不再遮挡；尝试={request['attempts']}；耗时={now - request['started']:.2f}秒")
        if not self.game or w.kind(self.game) != 'game':
            self.game = w.find('game')
        if not self.game or w.kind(self.game) != 'game':
            return self._finish_return('failed', '游戏窗口已不存在，无法继续返回')
        if now >= request['deadline']:
            return self._finish_return('failed', '恢复游戏超时：' + w.focus_diagnostics(self.game)
                                       + f"；Anki遮挡已清除={self._anki_cleared()}")
        # 最小化 Anki 首次露出的后台窗口不代表用户主动切换。
        # 只有随后明确更换了其他前台窗口且伴随新输入，才中止返回。
        if w.foreground() not in {'game', 'anki'} and not w.desktop_foreground():
            serial = w.input_serial() if hasattr(w, 'input_serial') else None
            handle = w.foreground_handle() if hasattr(w, 'foreground_handle') else 'other'
            if (request['other_window'] is not None and handle != request['other_window']
                    and serial is not None and request['other_serial'] is not None
                    and serial != request['other_serial']):
                return self._finish_return('cancelled', '检测到用户操作其他应用，停止返回重试')
            request['other_window'], request['other_serial'] = handle, serial
        if now < request['next_try']:
            return 'pending'
        request['attempts'] += 1
        request['next_try'] = now + 0.12
        if not self._game_ready():
            w.restore(self.game, activate=True)
        # 原生调用返回成功只表示请求已发送，仍检查真实前台与最小化状态。
        if self._game_ready() and self._anki_cleared():
            return self._finish_return('returned',
                f"已确认游戏取得前台且 Anki 不再遮挡；尝试={request['attempts']}；耗时={now - request['started']:.2f}秒")
        return 'pending'

    @property
    def owned(self):
        return self.anki is not None

    def show(self, anki, game, mode="auto", manual=False):
        w = self.windows
        if self.returning:
            self.last_reason = '游戏仍在恢复中，本次不重新置顶 Anki'
            return False
        if not anki or w.kind(anki) != "anki":
            self.last_reason = "Anki 主窗口不可用"
            return False
        if not manual and w.foreground() != "game":
            self.last_reason = "用户已经切离游戏，本次不抢占其他应用"
            return False
        self.anki, self.game = anki, game
        self.original_topmost = w.is_topmost(anki)
        self.minimized_game = False
        self.suspended = False
        self.focus_retries = 0
        self.next_focus_retry = 0
        try:
            w.set_topmost(anki, True)
            # 外部程序不能可靠区分所有独占全屏和无边框实现，自动档采用全屏兼容路径。
            if mode in {"auto", "fullscreen"} and game and not w.is_iconic(game):
                self.minimized_game = w.minimize(game)
            for attempt in range(3):
                if attempt and w.foreground() not in {"game", "anki"} and not w.desktop_foreground():
                    self.last_reason = "切换过程中用户转到其他应用，已取消"
                    self.release(activate=False)
                    return False
                w.set_topmost(anki, True)
                focused = w.activate(anki)
                if focused and w.is_topmost(anki):
                    self.last_reason = f"Anki 已置顶并取得焦点；模式={mode}；尝试={attempt + 1}"
                    return True
                # 无边框直接激活被游戏挡住时，同样尝试让出游戏显示。
                if game and not self.minimized_game and w.foreground() == "game":
                    self.minimized_game = w.minimize(game)
                if attempt < 2:
                    time.sleep(0.04)
            self.last_reason = "置顶或焦点切换未完成：" + w.focus_diagnostics(anki)
            self.release(activate=True)
            return False
        except Exception as exc:
            self.last_reason = f"窗口切换异常：{type(exc).__name__}: {exc}"
            self.release(activate=True)
            return False

    def release(self, activate=True):
        """始终恢复置顶状态；只从 Anki/桌面返回，不抢占其他应用。"""
        w = self.windows
        anki, game, minimized = self.anki, self.game, self.minimized_game
        original = self.original_topmost
        self._return = None
        self.anki = self.game = None
        self.minimized_game = False
        if anki and w.kind(anki) == "anki":
            w.set_topmost(anki, original)
        if not game or w.kind(game) != "game":
            return False
        front = w.foreground()
        if front == "game":
            return True
        if activate and (front == "anki" or w.desktop_foreground()):
            if anki and w.kind(anki) == 'anki' and not w.is_iconic(anki):
                w.minimize(anki)
            for attempt in range(3):
                if w.foreground() not in {'game', 'anki'} and not w.desktop_foreground():
                    break
                if w.restore(game, activate=True):
                    return True
                if attempt < 2:
                    time.sleep(0.04)
            return w.foreground() == 'game'
        if minimized and w.is_iconic(game):
            w.restore(game, activate=False)
        return False
