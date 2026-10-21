"""无界面应用服务：配置、异步任务、监测生命周期与事件队列。"""
import copy
import json
import math
from pathlib import Path
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from lol_words.targets.adapters import AdapterRegistry
from lol_words.system.capture import SceneCapture
from lol_words.game.game_api import GameData
from lol_words.system.native import Windows
from lol_words.targets.catalog import LearningCatalog, online_candidates
from lol_words.targets.software_catalog import scan_learning_apps, detected_target
from lol_words.modes.mode_config import migrate_config, validate_config, selected_spec, mode_key
from lol_words.application.monitoring import run_monitor, TARGET_STATES
from lol_words.modes.penalty import DeathLedger
from lol_words.ui.ui_state import RuntimeState
from lol_words.lobby.lobby_config import validate as validate_lobby
from lol_words.lobby.lobby_runtime import LobbyRuntime
from lol_words.lobby.lcu_api import LCUError


def validate_settings(config):
    validate_config(config)
    validate_lobby(config)
    for key, low, high in [('death_switch_delay', 0, 120), ('return_before_seconds', 0, 30),
                           ('gray_threshold', .5, .99), ('poll_seconds', .05, 5)]:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} 超出支持范围 {low}–{high}')
    if config['game_display_mode'] not in {'auto', 'borderless', 'fullscreen'}:
        raise ValueError('游戏显示方式无效')
    if type(config['confirmation_frames']) is not int or not 1 <= config['confirmation_frames'] <= 20:
        raise ValueError('连续确认帧数必须为 1–20 的整数')
    region = config['capture_region']
    if (not isinstance(region, list) or len(region) != 4 or
        any(type(v) not in (int, float) or not math.isfinite(v) for v in region) or
        not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1)):
        raise ValueError('采样区域必须是有效的比例矩形')


class ApplicationService:
    def __init__(self, root, windows=None):
        self.root = Path(root)
        self.windows = windows or Windows()
        self.events = queue.Queue()
        self.operation_lock, self.config_lock = threading.RLock(), threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='target-service')
        self.busy, self.futures = set(), set()
        self.jobs_lock = threading.Lock()
        self.worker = None
        self.lobby_worker = None
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.closing = False
        self.runtime = None
        self.epoch = 0
        self.discovered = set()
        self.availability = {}
        self.last_return = ''
        self.last_state = None
        self.config_error = ''
        self.registry = AdapterRegistry(self.windows, self.root, self.report)
        self.catalog = LearningCatalog(user_path=self.root / 'runtime/catalog-overrides.json')
        self.ledger = DeathLedger(self.root / 'runtime/penalty.json', self.report)
        path = self.root / 'config.json'
        if not path.exists():
            path = self.root / 'config.example.json'
        try:
            saved = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else None
            self.config = migrate_config(saved)
            validate_settings(self.config)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.config_error = '配置无法读取或校验失败。原文件未覆盖，请修正 config.json 后重新启动。'
            self.config = migrate_config()
            self.report(self.config_error)
        self.publish_state(RuntimeState(target_name=selected_spec(self.config)['name']))
        self.lobby = LobbyRuntime(self.windows,self.root,self.config_snapshot,self.report,
                                  lambda value:self.events.put(('lobby_state',value)))

    @property
    def running(self):
        return bool((self.worker and self.worker.is_alive()) or (self.lobby_worker and self.lobby_worker.is_alive()))

    def config_snapshot(self):
        with self.config_lock:return copy.deepcopy(self.config)

    def refresh_lobby(self):
        return self.submit('lobby_read', self.lobby.read_only)

    def on_monitor_exit(self):
        # 核心监测意外退出时，大厅线程也停止，避免界面已暂停却仍在接受对局。
        self.stop_event.set()

    def publish_state(self, state):
        if state != self.last_state:
            self.last_state = state
            self.events.put(('state', state))

    def report(self, text):
        self.events.put(('log', text))

    def update_config(self, changes=None, target=None):
        """校验、原子落盘成功后才发布新快照，保留未编辑字段。"""
        if self.config_error:
            raise ValueError(self.config_error)
        with self.config_lock:
            updated = copy.deepcopy(self.config)
            updated.update(copy.deepcopy(changes or {}))
            if target:
                updated['targets'] = [t for t in updated['targets'] if t['id'] != target['id']] + [copy.deepcopy(target)]
                updated['study_target'], updated['mode'] = target['id'], 'study'
            updated['persistent_anki_topmost'] = updated['persistent_target_topmost']
            validate_settings(updated)
            temporary = self.root / 'config.tmp'
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.root / 'config.json')
            changed = mode_key(self.config) != mode_key(updated)
            self.config = updated
            if changed:
                self.epoch += 1
                self.events.put(('selection', selected_spec(updated)['name']))
                self.report('目标或模式已保存；旧任务取消，等待下一次完整死亡。')
            self.events.put(('config', copy.deepcopy(updated)))
            return updated

    def submit(self, key, function):
        with self.jobs_lock:
            if self.closing or key in self.busy:
                return False
            self.busy.add(key)
        self.events.put(('job', (key, 'busy', None)))
        def work():
            try:
                result = function()
                self.events.put(('job', (key, 'ok', result)))
            except Exception as exc:
                self.events.put(('job', (key, 'error', self.friendly_error(key, exc))))
            finally:
                with self.jobs_lock:
                    self.busy.discard(key)
        future = self.pool.submit(work)
        with self.jobs_lock:
            self.futures = {f for f in self.futures if not f.done()}
            self.futures.add(future)
        return True

    @staticmethod
    def friendly_error(key, exc):
        if isinstance(exc,LCUError):return str(exc)
        if key == 'search':
            return '官网查询未完成，可能是网络连接或搜索响应异常。请重试，或使用内置目录。'
        if key == 'scan':
            return '读取安装信息失败或超时。请重试，或通过“添加桌面软件”选择程序。'
        return str(exc) if isinstance(exc, ValueError) else '操作未完成。请重试并检查目标配置及软件运行状态。'

    def scan(self):
        targets = copy.deepcopy(self.config['targets'])
        def work():
            # 采集失败与真正的零结果分别报告，仍保留 Anki 的常见位置发现。
            return scan_learning_apps(self.catalog, self.windows, self.root, configured_targets=targets, strict=True)
        return self.submit('scan', work)

    def search(self, name):
        self.catalog.search_target(name)  # 长度及空值校验
        def work():
            hits = self.catalog.search(name)
            if hits:
                return dict(kind='catalog', name=name, items=hits)
            return dict(kind='candidates', name=name, items=online_candidates(name))
        return self.submit('search', work)

    def choose_detected(self, item):
        return self.update_config(target=detected_target(item, self.config['targets']))

    def choose_web(self, item, name=None):
        entry = self.catalog.remember(name, item['url']) if name else item
        return self.update_config(target=self.catalog.web_target(entry))

    def inspect_target(self):
        spec, epoch = selected_spec(self.config), self.epoch
        def inspect():
            manager = self.registry.get(spec)
            key = (id(manager), mode_key({'mode': 'study', 'study_target': spec['id'], 'targets': [spec]}))
            if key not in self.discovered:
                # 安装目录发现可能较慢，不能占用死亡 / 返回流程的窗口操作锁。
                path = manager.adapter.discover()
                self.discovered.add(key)
                self.availability[spec['id']] = bool(path)
                updates = manager.adapter.configuration_updates(path) if path else {}
                if isinstance(updates, dict) and updates and not self.config_error:
                    with self.config_lock:
                        targets = copy.deepcopy(self.config['targets'])
                        original = next((t for t in targets if t['id'] == spec['id']), None)
                        if original == spec:
                            # 持久化已核实的发现结果，避免旧路径不断重建适配器，丢失复用预算。
                            original.update(updates)
                            self.update_config({'targets': targets})
            with self.operation_lock:
                manager.poll()
                missing = spec['adapter'] in {'desktop', 'anki'} and not self.availability.get(spec['id']) and manager.state == 'idle'
                return dict(epoch=epoch, name=spec['name'], state=TARGET_STATES.get(manager.state, '未知'),
                    reason=('未检测到启动文件，请检测学习软件或通过文件选择器添加。' if missing else
                            manager.last_failure if manager.state == 'failed' else manager.adapter.reason), missing=missing)
        return self.submit('inspect', inspect)

    def reopen_target(self):
        if self.closing or self.config_error:
            return False
        spec, epoch = selected_spec(self.config), self.epoch
        return self.submit('open', lambda: dict(epoch=epoch, message=self.manual_open(spec, epoch)))

    def manual_open(self, spec, epoch):
        with self.operation_lock:
            if epoch != self.epoch or self.closing:
                return '选择已变化，本次打开已取消。'
            manager = self.registry.get(spec)
            manager.ensure(manual=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            with self.operation_lock:
                if epoch != self.epoch or self.closing:
                    hwnd = manager.poll()
                    if hwnd and manager.freshly_started:
                        self.windows.minimize(hwnd)
                        manager.freshly_started = None
                    if not manager.pending_since:
                        return '选择已变化，本次显示已取消。'
                    # 迟到的启动窗口继续等待并仅隐藏，绝不执行过期显示。
                    cancelled = True
                else:
                    cancelled = False
                if cancelled:
                    hwnd = None
                else:
                    hwnd = manager.poll()
                if hwnd:
                    manager.freshly_started = None
                    success = self.windows.restore(hwnd, activate=True)
                    return '已显示目标窗口。' if success else '窗口已识别，但未获得前台。请点击任务栏中的目标窗口。'
                if not cancelled and manager.state in {'waiting', 'failed', 'closed'}:
                    raise ValueError(manager.last_failure or manager.adapter.reason)
            time.sleep(.15)
        raise ValueError('等待窗口超时。请检查软件提示，再手动重试。')

    def log_return_result(self, presentation, result):
        if result in {'returned', 'failed', 'cancelled'}:
            self.last_return = ('返回游戏成功：' if result == 'returned' else '返回游戏未完成：') + presentation.last_return_reason
            self.report(self.last_return)

    def start(self):
        if self.running or self.closing or self.config_error:
            return False
        self.stop_event = threading.Event()
        self.last_return = ''
        self.worker = threading.Thread(target=run_monitor, args=(self, copy.deepcopy(self.config), self.stop_event,
            self.root, GameData, SceneCapture), name='game-monitor', daemon=True)
        self.lobby_worker = threading.Thread(target=self.lobby.run,args=(self.stop_event,),name='lobby-monitor',daemon=True)
        self.lobby_worker.start()
        self.worker.start()
        self.report('自动监测已启动；目标首次需要时启动，手动关闭后不反复拉起。')
        return True

    def stop(self):
        self.stop_event.set()
        self.epoch += 1
        if self.running:
            self.publish_state(RuntimeState(monitoring='正在暂停', phase='正在清理', reason='正在取消待执行任务并恢复临时窗口状态。',
                target_name=selected_spec(self.config)['name']))

    def confirm_download(self, cancelled):
        reply, answer = threading.Event(), {'approved': False}
        self.events.put(('download_request', (reply, answer, cancelled)))
        deadline = time.monotonic() + 30
        while not reply.wait(.1):
            if cancelled.is_set() or self.closing or self.stop_event.is_set() or time.monotonic() >= deadline:
                cancelled.set()
                return False
        return bool(answer['approved'] and not cancelled.is_set() and not self.closing and not self.stop_event.is_set())

    def close(self):
        self.closing = True
        self.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def settled(self):
        with self.jobs_lock:
            self.futures = {f for f in self.futures if not f.done()}
            return not self.running and not self.futures
