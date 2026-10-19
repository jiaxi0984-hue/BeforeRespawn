"""死亡流程协调：状态计时、可信画面和窗口动作各自独立。"""
from lol_words.game.core import DeathSession, GrayGate, frame_stats


class StudyFlow:
    def __init__(self, windows, presentation, config, report, return_game):
        self.windows, self.presentation = windows, presentation
        self.config, self.report, self.return_game = config, report, return_game
        self.session = DeathSession(config['return_before_seconds'], config['death_switch_delay'])
        self.gate = GrayGate(config['gray_threshold'], config['confirmation_frames'])
        self.confirmed = False
        self.verify_attempts = 0
        self.next_verify = 0
        self.game_seen = None
        self.capture_after = 0
        self.last_wait = self.last_pin = self.last_backend = None
        self.now = self.last_wait_time = float('-inf')
        self.stopped = lambda: False
        self.on_shown = lambda: None

    def log_wait(self, key, text):
        if key != self.last_wait and (self.last_wait is None or self.now - self.last_wait_time >= 2):
            self.last_wait = key
            self.last_wait_time = self.now
            self.report(text)

    def data_lost(self):
        # 不让接口恢复后执行过期切出。若已在学习，则先解除置顶并返回。
        first_loss = not self.session.wait_reason.startswith('接口暂时失效')
        self.session.cancel('接口暂时失效，本次死亡取消切出，等待下一次完整死亡')
        self.confirmed = False
        self.gate.consecutive = 0
        self.game_seen = None
        if first_loss:
            self.report(self.session.wait_reason)
        if self.presentation.owned and not self.presentation.returning:
            self.return_game()
        self.session.engaged = False

    def step(self, sample, now, target, capture, mode):
        s, p, w = self.session, self.presentation, self.windows
        lazy_target = callable(target)
        self.now = now
        new_death = s.previous is False and sample.dead
        if new_death:
            self.confirmed = False
            self.verify_attempts = 0
            self.next_verify = now
            self.last_wait = None
            self.last_wait_time = float('-inf')
            self.report(f'本人死亡已确认：单调时间={now:.3f}；英雄={sample.champion}；'
                        f'剩余={sample.timer:.1f}秒；切出延迟={s.delay:g}秒；复活前返回={s.lead:g}秒')
        if not sample.dead:
            self.confirmed = False
        observation = p.observe_foreground(now)
        if p.last_pin_status and p.last_pin_status != self.last_pin:
            self.last_pin = p.last_pin_status
            self.report('目标窗口 置顶状态：' + self.last_pin)
        foreground = w.foreground()
        action = s.update(sample, foreground, False, now=now)
        if action == 'return':
            self.return_game()
        if sample.dead and (s.finished or s.engaged or s.attempted):
            if s.finished:
                self.log_wait(s.wait_reason, s.wait_reason)
            return observation

        # 只分析当前实际前台游戏的画面，绝不读取被其他应用覆盖的桌面区域。
        game = w.current_game()
        if game != self.game_seen:
            self.game_seen = game
            self.gate.consecutive = 0
            self.capture_after = now + 0.3 if sample.dead else now
        stats = None
        if game and not w.is_iconic(game) and now >= self.capture_after:
            pixels = capture.read(game, self.config['capture_region'])
            if pixels and w.current_game() == game and not w.is_iconic(game):
                stats = frame_stats(pixels)
            backend = getattr(capture, 'backend', '测试采样')
            if backend != self.last_backend:
                self.last_backend = backend
                self.report('画面采样：' + backend + '；' + getattr(capture, 'detail', ''))
        gray = self.gate.observe(sample.dead, stats)
        if gray and not self.confirmed:
            self.confirmed = True
            self.report('本次死亡灰屏已连续确认：' + self.gate.label + '；保留证据至本次死亡结束')
        if not sample.dead:
            self.last_wait = None
            return observation
        if lazy_target:
            target = target(now) if self.confirmed and now - s.dead_since >= s.delay and not p.returning and not self.stopped() else None
        action = s.update(sample, w.foreground(), self.confirmed and bool(target), now=now)
        if action == 'study':
            if not target or p.returning or self.stopped():
                s.confirm_study(False)
                return observation
            game = w.find('game')
            if not game:
                s.confirm_study(False)
                self.report('游戏窗口已不存在，取消本次死亡学习切换')
                return observation
            success = p.show(target, game, mode, persistent_pin=self.config.get('persistent_target_topmost', self.config.get('persistent_anki_topmost', True)))
            s.confirm_study(success)
            if success:
                self.on_shown()
            self.report('死亡学习切换：' + p.last_reason)
            return observation
        if now - s.dead_since < s.delay:
            self.log_wait('delay', f'切出等待：死亡后延迟 {s.delay:g} 秒；期间保留购买装备、选择海克斯的操作时间')
        elif not target and (not lazy_target or self.confirmed):
            self.log_wait('target', '切出等待：目标窗口当前未运行或主窗口未就绪，可点击“手动重新打开目标”')
        elif not self.confirmed and not game:
            if self.gate.reference is None:
                self.log_wait('reference', '切出等待：缺少存活彩色参照，无法安全确认灰屏；请存活时让游戏处于前台')
            elif self.verify_attempts < 3 and now >= self.next_verify:
                self.verify_attempts += 1
                self.next_verify = now + 2
                target_game = w.find('game')
                self.report(f'后台无法确认灰屏：恢复游戏采集新画面（{self.verify_attempts}/3）；不使用倒计时推断可操作阶段结束')
                if target_game and not self.stopped():
                    w.restore(target_game, activate=True)
                self.game_seen = None
                self.gate.consecutive = 0
            elif self.verify_attempts >= 3:
                self.log_wait('verify_failed', '切出等待：恢复游戏采样未完成，不再抢焦点；手动返回游戏后仍可继续确认灰屏')
        else:
            self.log_wait(('visual', stats is not None), '切出等待：' + self.gate.label)
        return observation

    def cleanup(self):
        self.session.cancel('本局任务已清理')
        self.session.engaged = False
        self.confirmed = False
        self.presentation.release(activate=False)
        self.report('已清理本局死亡计时、切换任务和临时置顶；恢复 目标窗口 原置顶状态')
