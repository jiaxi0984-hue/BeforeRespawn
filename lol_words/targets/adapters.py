"""可替换目标适配器；仅目标层了解软件名称和进程匹配规则。"""
from pathlib import Path
import os
import subprocess
import threading
import time
from typing import Protocol
from lol_words.targets.discovery import locate_anki
from lol_words.system.external_process import popen_external


def normalized(path):
    return os.path.normcase(os.path.abspath(path)).casefold() if path else ''


class TargetAdapter(Protocol):
    spec: dict
    reason: str
    def discover(self) -> str: ...
    def running(self) -> bool: ...
    def start(self): ...
    def find(self) -> int | None: ...
    def configuration_updates(self, path) -> dict: ...


class DesktopAdapter:
    def __init__(self, spec, windows, root):
        self.spec, self.windows, self.root = spec, windows, Path(root)
        self.cached = None
        self.bound = False
        self.reason = ''

    def discover(self):
        command = self.spec.get('command', [])
        return command[0] if command and Path(command[0]).is_file() else ''

    def matches(self, item):
        return (normalized(item['path']) == normalized(self.spec.get('process_path'))
                and self.spec.get('title_contains', '').casefold() in item['title'].casefold()
                and (not self.spec.get('window_class') or self.spec['window_class'] == item['window_class']))

    def find(self):
        matches = [item for item in self.windows.window_inventory() if self.matches(item)]
        if self.cached:
            for item in matches:
                if (item['hwnd'], item['pid'], item['created']) == self.cached:
                    self.reason = '复用已锁定窗口'
                    return item['hwnd']
        if self.bound:
            self.reason = '原先锁定的窗口已关闭或身份变化；不自动改选其他窗口，可手动重新打开'
            return None
        self.cached = None
        if len(matches) == 1:
            item = matches[0]
            self.cached = item['hwnd'], item['pid'], item['created']
            self.bound = True
            self.reason = '唯一进程路径和窗口条件匹配'
            return item['hwnd']
        self.reason = f'匹配到 {len(matches)} 个主窗口，请细化窗口条件' if matches else '尚未找到匹配主窗口'
        return None

    def running(self):
        path = normalized(self.spec.get('process_path'))
        if not path:
            return False
        basename = Path(self.spec['process_path']).name.casefold()
        for pid, name in self.windows.processes():
            if name == basename:
                identity = self.windows.process_identity(pid)
                if identity and normalized(identity['path']) == path:
                    return True
        return False

    def configuration_updates(self, path):
        return {}

    def start(self):
        command = self.spec.get('command', [])
        if not command or not Path(command[0]).is_file():
            raise ValueError('未发现目标启动文件，请自动发现或通过文件选择添加')
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 7  # 启动初始化期间不主动激活，真正显示交给统一窗口管理。
        return popen_external(command, cwd=str(Path(command[0]).parent), shell=False, startupinfo=info)


class AnkiAdapter(DesktopAdapter):
    def __init__(self, *args):
        super().__init__(*args)
        self.allowed_paths = set()

    def discover(self):
        running = self.windows.anki_paths()
        self.allowed_paths.update(normalized(path) for path in running)
        command = self.spec.get('command', [])
        found = locate_anki(command[0] if command else '', self.root, running)
        if found:
            self.spec['command'] = [found]
            self.allowed_paths.add(normalized(found))
        return found

    def matches(self, item):
        path = normalized(item['path'])
        # 运行元数据来自精确 Anki 可执行文件；不按模糊标题认其他软件。
        return (path in self.allowed_paths
                and (not self.spec.get('title_contains') or self.spec['title_contains'].casefold() in item['title'].casefold())
                and (not self.spec.get('window_class') or self.spec['window_class'] == item['window_class']))

    def find(self):
        self.allowed_paths.update(normalized(path) for path in self.windows.anki_paths())
        return super().find()

    def running(self):
        return bool(self.windows.anki_paths())

    def configuration_updates(self, path):
        return {'command': [path]} if path else {}


class TargetWindows:
    """把目标窗口映射为统一 target 类型，窗口管理不依赖具体软件。"""
    def __init__(self, native, adapter):
        self.native, self.adapter = native, adapter

    def __getattr__(self, name):
        return getattr(self.native, name)

    def kind(self, hwnd):
        if hwnd and hwnd == self.adapter.find():
            return 'target'
        return 'game' if self.native.kind(hwnd) == 'game' else 'other'

    def foreground(self):
        return self.kind(self.native.foreground_handle())

    def find(self, kind):
        return self.adapter.find() if kind == 'target' else self.native.find(kind)


class TargetManager:
    def __init__(self, adapter: TargetAdapter, report):
        self.adapter, self.report = adapter, report
        self.attempted = False
        self.pending_since = None
        self.state = ''
        self.lock = threading.RLock()
        self.freshly_started = None
        self.last_failure = ''

    def log(self, key, message):
        if key != self.state:
            self.state = key
            self.report(f"{self.adapter.spec['name']}：{message}")

    def poll(self):
        with self.lock:
            hwnd = self.adapter.find()
            if hwnd:
                if self.pending_since is not None:
                    self.freshly_started = hwnd
                self.attempted = True
                self.pending_since = None
                self.log('ready', '复用已识别窗口；结束对局保留')
            elif self.adapter.running():
                self.attempted = True
                self.log('waiting', self.adapter.reason + '；不重复启动')
            elif self.pending_since is not None and time.monotonic() - self.pending_since < 45:
                self.log('starting', '启动中，不重复拉起')
            else:
                self.pending_since = None
                if self.state != 'failed':
                    self.log('closed' if self.attempted else 'idle', '未运行；自动启动机会已用完，可手动重新打开' if self.attempted else '首次满足安全切出条件时启动')
            return hwnd

    def ensure(self, manual=False):
        with self.lock:
            if manual:
                self.adapter.cached = None
                self.adapter.bound = False
            hwnd = self.poll()
            if hwnd or self.adapter.running() or self.pending_since is not None:
                return hwnd
            if self.attempted and not manual:
                return None
            self.attempted = True
            try:
                self.adapter.start()
                self.pending_since = time.monotonic()
                self.log('starting', '已请求启动，等待唯一主窗口')
            except (OSError, ValueError) as exc:
                self.last_failure = str(exc)
                self.log('failed', self.last_failure)
            return self.poll()


class AdapterRegistry:
    def __init__(self, windows, root, report):
        self.windows, self.root, self.report = windows, Path(root), report
        self.managers = {}
        self.lock = threading.RLock()

    def get(self, spec):
        with self.lock:
            import copy
            key = spec['id']
            old = self.managers.get(key)
            effective = copy.deepcopy(spec)
            if old and not effective.get('command') and old.adapter.spec.get('command'):
                effective['command'] = old.adapter.spec['command']
            if not old or old.adapter.spec != effective:
                if spec['adapter'] == 'web':
                    from lol_words.targets.browser_target import BrowserAdapter
                    adapter = BrowserAdapter(effective, self.windows, self.root)
                else:
                    cls = AnkiAdapter if spec['adapter'] == 'anki' else DesktopAdapter
                    adapter = cls(effective, self.windows, self.root)
                manager = TargetManager(adapter, self.report)
                if old:
                    manager.attempted, manager.pending_since = old.attempted, old.pending_since
                # 旧窗口上下文仍持有旧适配器，能正确恢复其临时置顶后再切换。
                self.managers[key] = manager
            return self.managers[key]
