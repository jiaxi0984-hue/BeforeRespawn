"""独立管理窗口层级与焦点，返回、暂停和退出时恢复原置顶偏好。"""
import time


class StudyWindow:
    def __init__(self, windows, target_kind="target"):
        self.windows = windows
        self.target_kind = target_kind
        self.target = self.game = None
        self.original_topmost = False
        self.minimized_game = False
        self.last_reason = ""
        self._return = None
        self.last_return_reason = ""
        self.suspended = False
        self.persistent_pin = True
        self.next_pin_check = 0
        self.last_pin_status = ""

    @property
    def returning(self):
        return self._return is not None

    def _game_ready(self):
        target = self.windows.current_game()
        return bool(target and self.windows.kind(target) == 'game'
                    and not self.windows.is_iconic(target))

    def _target_cleared(self):
        w = self.windows
        return (not self.target or w.kind(self.target) != self.target_kind or w.is_iconic(self.target)
                or not w.is_visible(self.target))

    def observe_foreground(self, now):
        """窗口焦点不等同于画面遮挡；无边框下保留返回记录。"""
        if not self.owned or self.returning:
            return 'idle'
        w = self.windows
        if self._target_cleared():
            # 用户隐藏目标窗口也保留本次复活的返回记录。
            return 'target_hidden'
        front = w.foreground()
        if self.persistent_pin:
            if now >= self.next_pin_check:
                self.next_pin_check = now + 0.5
                ok = w.set_topmost(self.target, True)
                self.last_pin_status = '持续置顶正常（不改变键盘焦点）' if ok and w.is_topmost(self.target) else '持续置顶请求失败，请检查权限'
            return 'game_under_target' if front == 'game' else front
        if front == 'game':
            #目标窗口仍可见，不能仅凭游戏短暂取得焦点认定用户完成了返回。
            return 'game_under_target'
        if front == self.target_kind:
            if self.suspended:
                w.set_topmost(self.target, self.original_topmost)
                self.suspended = False
            return self.target_kind
        # 暂时切去其他应用只取消覆盖，不丢弃稍后返回所需的记录。
        if not self.suspended:
            w.set_topmost(self.target, self.original_topmost)
            self.suspended = True
        return 'other'

    def _hide_target(self, now):
        if self._target_cleared() or now < self._return['next_hide']:
            return
        self._return['next_hide'] = now + 0.4
        if self.windows.minimize(self.target):
            self._return['minimized_target'] = True

    def request_return(self, now):
        """发送恢复请求后保留窗口记录，直到真正取得前台或超时。"""
        if self.returning:
            return 'pending'
        if not self.owned:
            return 'idle'
        w = self.windows
        if (w.kind(self.target) == self.target_kind
                and w.is_topmost(self.target) != self.original_topmost):
            w.set_topmost(self.target, self.original_topmost)
        self._return = dict(started=now, deadline=now + 3.0, next_try=now,
                            attempts=0, minimized_target=False, next_hide=now)
        # 对称处理：先让目标窗口让出前台，而不是仅取消置顶后仍留在游戏前面。
        self._hide_target(now)
        return self.poll_return(now)

    def _finish_return(self, result, reason):
        w, request = self.windows, self._return
        if result != 'returned' and request and request['minimized_target']:
            if self.target and w.kind(self.target) == self.target_kind:
                w.restore(self.target, activate=False)
        self.last_return_reason = reason
        self._return = None
        self.target = self.game = None
        self.minimized_game = False
        return result

    def poll_return(self, now):
        if not self.returning:
            return 'idle'
        w, request = self.windows, self._return
        self._hide_target(now)
        if self._game_ready() and self._target_cleared():
            return self._finish_return('returned',
                f"已确认游戏取得前台且目标窗口不再遮挡；尝试={request['attempts']}；耗时={now - request['started']:.2f}秒")
        if not self.game or w.kind(self.game) != 'game':
            self.game = w.find('game')
        if not self.game or w.kind(self.game) != 'game':
            return self._finish_return('failed', '游戏窗口已不存在，无法继续返回')
        if now >= request['deadline']:
            return self._finish_return('failed', '恢复游戏超时：' + w.focus_diagnostics(self.game)
                                       + f"；目标窗口遮挡已清除={self._target_cleared()}")
        # 死亡学习已建立的返回任务不因其他应用取得前台而丢失，重试仍限于 3 秒。
        if now < request['next_try']:
            return 'pending'
        request['attempts'] += 1
        request['next_try'] = now + 0.12
        if not self._game_ready():
            w.restore(self.game, activate=True)
        # 原生调用返回成功只表示请求已发送，仍检查真实前台与最小化状态。
        if self._game_ready() and self._target_cleared():
            return self._finish_return('returned',
                f"已确认游戏取得前台且目标窗口不再遮挡；尝试={request['attempts']}；耗时={now - request['started']:.2f}秒")
        return 'pending'

    @property
    def owned(self):
        return self.target is not None

    def show(self, target, game, mode="auto", manual=False, persistent_pin=True):
        w = self.windows
        if self.returning:
            self.last_reason = '游戏仍在恢复中，本次不重新置顶目标窗口'
            return False
        if not target or w.kind(target) != self.target_kind:
            self.last_reason = "目标窗口主窗口不可用"
            return False
        self.target, self.game = target, game
        self.original_topmost = w.is_topmost(target)
        self.minimized_game = False
        self.suspended = False
        self.persistent_pin = persistent_pin
        self.next_pin_check = 0
        self.last_pin_status = ""
        try:
            w.set_topmost(target, persistent_pin or self.original_topmost)
            # 外部程序不能可靠区分所有独占全屏和无边框实现，自动档采用全屏兼容路径。
            if mode in {"auto", "fullscreen"} and game and not w.is_iconic(game):
                self.minimized_game = w.minimize(game)
            for attempt in range(3):
                w.set_topmost(target, persistent_pin or self.original_topmost)
                focused = w.activate(target)
                if focused and (not persistent_pin or w.is_topmost(target)):
                    self.last_reason = f"目标窗口已显示并取得焦点；持续置顶={persistent_pin}；模式={mode}；尝试={attempt + 1}"
                    return True
                # 无边框直接激活被游戏挡住时，同样尝试让出游戏显示。
                if game and not self.minimized_game and w.foreground() == "game":
                    self.minimized_game = w.minimize(game)
                if attempt < 2:
                    time.sleep(0.04)
            self.last_reason = "置顶或焦点切换未完成：" + w.focus_diagnostics(target)
            self.release(activate=True)
            return False
        except Exception as exc:
            self.last_reason = f"窗口切换异常：{type(exc).__name__}: {exc}"
            self.release(activate=True)
            return False

    def release(self, activate=True):
        """始终恢复置顶状态；只从目标窗口/桌面返回，不抢占其他应用。"""
        w = self.windows
        target, game, minimized = self.target, self.game, self.minimized_game
        original = self.original_topmost
        self._return = None
        self.target = self.game = None
        self.minimized_game = False
        if target and w.kind(target) == self.target_kind:
            w.set_topmost(target, original)
        if not game or w.kind(game) != "game":
            return False
        front = w.foreground()
        if front == "game":
            return True
        if activate and (front == self.target_kind or w.desktop_foreground()):
            if target and w.kind(target) == self.target_kind and not w.is_iconic(target):
                w.minimize(target)
            for attempt in range(3):
                if w.foreground() not in {'game', self.target_kind} and not w.desktop_foreground():
                    break
                if w.restore(game, activate=True):
                    return True
                if attempt < 2:
                    time.sleep(0.04)
            return w.foreground() == 'game'
        if minimized and w.is_iconic(game):
            w.restore(game, activate=False)
        return False
