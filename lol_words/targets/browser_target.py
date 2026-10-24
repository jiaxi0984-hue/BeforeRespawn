"""专用 Chromium 应用窗口：独立配置目录、精确进程身份，不碰日常标签页。"""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit
from lol_words.targets.adapters import DesktopAdapter, normalized
from lol_words.system.state_store import JsonStore
from lol_words.system.external_process import popen_external


def find_browser():
    candidates = []
    for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        root = Path(os.environ.get(variable, 'C:/nonexistent'))
        candidates += [root / 'Microsoft/Edge/Application/msedge.exe', root / 'Google/Chrome/Application/chrome.exe']
    return next((str(p.resolve()) for p in candidates if p.is_file()), '')


class BrowserAdapter(DesktopAdapter):
    def __init__(self, spec, windows, root):
        super().__init__(spec, windows, root)
        import hashlib
        key = hashlib.sha256(spec['id'].encode()).hexdigest()[:20]
        self.profile = self.root / 'runtime' / 'browsers' / key
        self.owner = JsonStore(self.profile / 'owner.json')
        self.process = None

    def discover(self):
        return find_browser()

    def identity(self):
        try:
            expected = self.owner.read().get('identity')
            actual = self.windows.process_identity(expected['pid']) if expected else None
            return actual if actual == expected else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def matches(self, item):
        owner = self._match_owner if hasattr(self, '_match_owner') else self.identity()
        return bool(owner and item['pid'] == owner['pid'] and item['created'] == owner['created']
                    and normalized(item['path']) == normalized(owner['path'])
                    and item['window_class'].startswith('Chrome_WidgetWin'))

    def find(self):
        self._match_owner = self.identity()
        if not self._match_owner:
            self.reason = '专用浏览器未运行，或进程身份无法可靠确认'
            return None
        return super().find()

    def running(self):
        return self.identity() is not None

    def start(self):
        if self.running():
            return
        browser = self.discover()
        if not browser:
            raise ValueError('未发现 Edge 或 Chrome；网页模式需要安装其中一个浏览器')
        self.profile.mkdir(parents=True, exist_ok=True)
        command = [browser, '--user-data-dir=' + str(self.profile.resolve()), '--app=' + self.spec['url'],
                   '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
                   '--remote-debugging-address=127.0.0.1']
        self.process = popen_external(command, shell=False)
        identity = self.windows.process_identity(self.process.pid)
        if not identity:
            raise OSError('无法确认专用浏览器进程身份，不按浏览器名称操作其他窗口')
        def update(data):
            data.clear()
            data.update(identity=identity)
        self.owner.transact(update)

    def page(self):
        """只访问本工具独立配置目录对应的随机回环端口。"""
        if not self.identity():
            raise OSError('专用浏览器未运行或身份已改变')
        lines = (self.profile / 'DevToolsActivePort').read_text(encoding='utf-8').splitlines()
        port = int(lines[0])
        if not 1024 <= port <= 65535:
            raise ValueError('专用浏览器端口无效')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/json/list', timeout=0.8) as response:
            pages = json.load(response)
        owner = self.owner.read()
        selected = owner.get('target_id')
        expected_origin = urlsplit(self.spec['url'])
        matches = [p for p in pages if p.get('type') == 'page' and
                   (p.get('id') == selected if selected else
                    (urlsplit(p.get('url', '')).scheme, urlsplit(p.get('url', '')).netloc) ==
                    (expected_origin.scheme, expected_origin.netloc))]
        if len(matches) != 1:
            raise OSError('无法唯一识别本工具创建的页面；不操作其他标签页')
        page = matches[0]
        ws = urlsplit(page['webSocketDebuggerUrl'])
        if ws.scheme != 'ws' or ws.hostname not in {'localhost', '127.0.0.1'} or ws.port != port:
            raise ValueError('浏览器调试连接不属于专用本地端口')
        if not selected:
            self.owner.transact(lambda data: data.update(target_id=page['id']))
        return page

    def command(self, method, params):
        # 依赖加载范围仅限项目 _deps，不加载任何浏览器凭据或已有用户配置目录。
        import sys
        deps = str(self.root / '_deps')
        if not getattr(sys,'frozen',False) and Path(deps).is_dir() and deps not in sys.path:
            sys.path.insert(0, deps)
        import websocket
        page = self.page()
        sock = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=1.5,
                                            suppress_origin=True, http_no_proxy=['127.0.0.1', 'localhost'])
        try:
            sock.send(json.dumps(dict(id=1, method=method, params=params)))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = json.loads(sock.recv())
                if result.get('id') == 1:
                    if 'error' in result:
                        raise ValueError('官方页面脚本检查失败')
                    return result.get('result', {})
            raise OSError('专用页面响应超时')
        finally:
            sock.close()

    def evaluate(self, expression):
        result = self.command('Runtime.evaluate', dict(expression=expression, returnByValue=True, userGesture=True))
        if result.get('exceptionDetails'):
            raise ValueError('官方页面脚本检查失败')
        return result.get('result', {}).get('value')

    def navigate_home(self):
        """仅导航本工具已识别的页面，不新建或改动日常标签页。"""
        if self.page().get('url', '').rstrip('/') == self.spec['url'].rstrip('/'):
            return
        result = self.command('Page.navigate', dict(url=self.spec['url']))
        if result.get('errorText'):
            raise OSError('官网导航未完成')

    def request_download(self, url):
        result = self.command('Page.navigate', dict(url=url))
        if result.get('errorText') not in (None, '', 'net::ERR_ABORTED'):
            raise OSError('浏览器未能提交官方下载')
