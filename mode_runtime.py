"""模式协调：目标选择与惩罚调度不进入死亡状态机。"""
import copy
import threading
import time
from adapters import AdapterRegistry, TargetWindows
from mode_config import GENSHIN_HOME, mode_key, selected_spec
from official_download import OfficialDownload
from presentation import StudyWindow
from study_flow import StudyFlow


class ModeRuntime:
    def __init__(self, windows, root, config, ledger, report, on_return=lambda *args: None, registry=None):
        self.native, self.root, self.ledger, self.report = windows, root, ledger, report
        self.registry = registry or AdapterRegistry(windows, root, report)
        self.on_return = on_return
        self.config = copy.deepcopy(config)
        self.primary = self.manager = self.registry.get(selected_spec(config))
        self.context = TargetWindows(windows, self.manager.adapter)
        self.presentation = StudyWindow(self.context)
        self.flow = self.make_flow(config)
        self.key = mode_key(config)
        self.download = None
        self.penalty_death = None
        self.current_death = None
        self.stopped = lambda: False
        self.download_factory = OfficialDownload
        self.retired_pending = []

    def make_flow(self, config):
        flow = StudyFlow(self.context, self.presentation, copy.deepcopy(config), self.report, self.return_game)
        flow.on_shown = self.on_shown
        return flow

    def switch(self, config):
        key = mode_key(config)
        if key == self.key:
            # 仅显示模式允许即时生效；公共计时参数保持当前监测快照。
            self.config['game_display_mode'] = config['game_display_mode']
            return False
        self.cleanup()
        self.config.update(mode=config['mode'], study_target=config['study_target'], targets=copy.deepcopy(config['targets']),
                           entertainment_site=config['entertainment_site'], death_penalty_enabled=config['death_penalty_enabled'])
        self.primary = self.manager = self.registry.get(selected_spec(self.config))
        self.context.adapter = self.manager.adapter
        self.flow = self.make_flow(self.config)
        self.flow.session.cancel('模式或目标已切换，本次死亡取消切出，等待下一次完整死亡')
        # 第一个新样本若仍死亡，只观察；若存活则下一次死亡正常处理。
        self.key = key
        self.penalty_death = self.current_death = None
        self.report('模式已切换：' + ('学习模式' if config['mode'] == 'study' else '娱乐模式') + '；旧目标临时置顶和任务已清理')
        return True

    def prepare_target(self, now):
        if self.stopped():
            return None
        if self.ledger.eligible(self.config['mode'], self.config['death_penalty_enabled']):
            if self.ledger.claim():
                self.penalty_death = self.current_death
                spec = dict(id='genshin-penalty', name='原神官网（死亡惩罚）', adapter='web', url=GENSHIN_HOME, pin=True)
                self.manager = self.registry.get(spec)
                self.context.adapter = self.manager.adapter
                self.download = self.download_factory(self.report)
                self.report('死亡惩罚已为本局记账：本次优先原神官网，普通娱乐页面不会同时弹出')
        if self.penalty_death != self.current_death and self.manager is not self.primary:
            self.manager = self.primary
            self.context.adapter = self.manager.adapter
        self.flow.config['persistent_target_topmost'] = bool(self.config['persistent_target_topmost'] and self.manager.adapter.spec.get('pin', True))
        target = self.manager.ensure()
        if (self.penalty_death is not None and self.penalty_death == self.current_death and self.ledger.current and self.manager.state == 'failed'
                and self.ledger.current['status'] != '已触发，但专用浏览器不可用；未发起下载，不自动重试'):
            self.ledger.set_status('已触发，但专用浏览器不可用；未发起下载，不自动重试')
        return target

    def on_shown(self):
        self.manager.freshly_started = None
        if self.penalty_death == self.current_death and self.download:
            self.download.start(self.manager.adapter, self.ledger)

    def step(self, name, sample, now, game_time, process_key, wall_time, capture):
        self.ledger.observe(name, game_time, process_key, sample.deaths, wall_time)
        if sample.dead and self.flow.session.previous is False:
            self.current_death = now
        self.manager.poll()
        just_started = self.manager.freshly_started
        if just_started and (self.flow.session.finished or not sample.dead or sample.timer <= self.config['return_before_seconds'] + 0.5):
            # 外部程序启动较慢时，只隐藏本工具刚启动的目标，不执行过期切出。
            self.native.minimize(just_started)
            self.manager.freshly_started = None
        self.flow.stopped = self.stopped
        self.flow.step(sample, now, self.prepare_target, capture, self.config['game_display_mode'])

    def return_game(self):
        if self.download:
            self.download.cancel()
        result = self.presentation.request_return(self.flow.now)
        self.on_return(self.presentation, result)

    def poll_return(self, now):
        for manager, deadline in list(self.retired_pending):
            target = manager.poll()
            if target and manager.freshly_started:
                self.native.minimize(target)
                manager.freshly_started = None
                self.report('旧模式迟到的目标窗口已最小化，未执行过期切出')
            if target or now >= deadline:
                self.retired_pending.remove((manager, deadline))
        result = self.presentation.poll_return(now)
        self.on_return(self.presentation, result)
        return result

    def data_lost(self):
        if self.download:
            self.download.cancel()
        self.flow.data_lost()

    def cleanup(self):
        if self.download:
            self.download.cancel()
        if self.manager.pending_since is not None and not any(m is self.manager for m,d in self.retired_pending):
            self.retired_pending.append((self.manager, time.monotonic() + 45))
        self.flow.cleanup()

    def end_match(self):
        self.cleanup()
        self.ledger.end()
        self.manager = self.primary
        self.context.adapter = self.manager.adapter
        self.flow = self.make_flow(self.config)
        self.penalty_death = self.current_death = None
        self.report('对局结束，学习软件与专用网页均保留；等待下一局')
