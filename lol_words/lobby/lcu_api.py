"""客户端连接、严格能力探测与大厅操作。认证仅驻留内存，请求仅限回环。"""
from dataclasses import dataclass, field
import base64
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from lol_words.game.game_api import NoRedirect
from lol_words.system.privileges import is_elevated, process_elevated

PHASE = '/lol-gameflow/v1/gameflow-phase'
SESSION = '/lol-gameflow/v1/session'
LOBBY = '/lol-lobby/v2/lobby'
READY = '/lol-matchmaking/v1/ready-check'
SELF = '/lol-summoner/v1/current-summoner'
PLAY_AGAIN = '/lol-lobby/v2/play-again'
ACCEPT = '/lol-matchmaking/v1/ready-check/accept'
OPERATIONS = {
    'return': ('PostLolLobbyV2PlayAgain', PLAY_AGAIN),
    'accept': ('PostLolMatchmakingV1ReadyCheckAccept', ACCEPT),
    'promote_v3': ('PostLolLobbyV3LobbyMembersByPuuidPromote', '/lol-lobby/v3/lobby/members/{puuid}/promote'),
    'promote_v2': ('PostLolLobbyV2LobbyMembersBySummonerIdPromote', '/lol-lobby/v2/lobby/members/{summonerId}/promote'),
}


class LCUError(Exception):
    def __init__(self, reason='连接不可用', status=None, uncertain=False, needs_admin=False):
        self.status, self.uncertain = status, uncertain
        self.needs_admin=needs_admin
        super().__init__(f'{reason}（HTTP {status}）' if status else reason)


@dataclass(frozen=True)
class Connection:
    pid: int
    port: int
    token: str = field(repr=False)

    @property
    def key(self):
        return self.pid, self.port, hashlib.sha256(self.token.encode()).hexdigest()


def parse_lockfile(text, live_pids):
    fields = text.strip().split(':')
    if len(fields) != 5 or fields[0].casefold() != 'leagueclient' or fields[4] != 'https':
        return None
    try:
        pid, port = int(fields[1]), int(fields[2])
    except ValueError:
        return None
    if pid not in live_pids or not 1 <= port <= 65535 or not fields[3] or len(fields[3]) > 512:
        return None
    return Connection(pid, port, fields[3])


def parse_commandline(pid, command, live_pids):
    if pid not in live_pids or not isinstance(command, str):
        return None
    port = re.search(r'(?:^|\s|\")--app-port=(\d+)(?:\s|\"|$)', command)
    token = re.search(r'(?:^|\s|\")--remoting-auth-token=([^\s\"]+)', command)
    if not port or not token or not 1 <= int(port[1]) <= 65535 or len(token[1]) > 512:
        return None
    return Connection(pid, int(port[1]), token[1])


class ClientDiscovery:
    def __init__(self, windows):
        self.windows = windows
        self.cached = None
        self.next_query = 0
        self.reason = '等待客户端启动'
        self.needs_admin=False

    def find(self, now):
        processes = [(pid, name) for pid, name in self.windows.processes()
                     if name in {'leagueclient.exe', 'leagueclientux.exe'}]
        pids = {pid for pid, _ in processes}
        if not pids:
            self.cached = None
            self.reason = '等待客户端启动'
            self.next_query = 0
            self.needs_admin=False
            return None
        if now < self.next_query and self.cached and self.cached.pid in pids:
            return self.cached
        if now < self.next_query:
            return None
        self.next_query = now + 3
        self.needs_admin=False
        roots = []
        for pid, _ in processes:
            handle = self.windows.k.OpenProcess(0x1000, False, pid)
            if handle:
                try:
                    text, size = C.create_unicode_buffer(32768), W.DWORD(32768)
                    if self.windows.k.QueryFullProcessImageNameW(handle, 0, text, C.byref(size)):
                        roots.append(Path(text.value).parent)
                finally:
                    self.windows.k.CloseHandle(handle)
        # 腾讯客户端可能只保留空 lockfile，安装目录还可从注册表取得。
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (r'SOFTWARE\WOW6432Node\Tencent\LOL', r'SOFTWARE\Tencent\LOL',
                        r'SOFTWARE\WOW6432Node\Riot Games\League of Legends'):
                try:
                    with winreg.OpenKey(hive, key) as registry:
                        for name in ('InstallPath', 'InstallLocation', 'Location', 'Path'):
                            try:
                                value = winreg.QueryValueEx(registry, name)[0]
                                if isinstance(value, str) and value:
                                    roots.append(Path(value))
                            except OSError:
                                pass
                except OSError:
                    pass
        for root in dict.fromkeys(roots):
            for path in (root / 'lockfile', root / 'LeagueClient' / 'lockfile'):
                try:
                    connection = parse_lockfile(path.read_text(encoding='utf-8-sig'), pids)
                    if connection:
                        self.cached = connection
                        return connection
                except (OSError, ValueError):
                    pass
        # 只查询 LeagueClientUx；命令行中的认证参数不回显、不写磁盘。
        script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @(Get-CimInstance Win32_Process -Filter \"Name = 'LeagueClientUx.exe'\" | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            records = json.loads(result.stdout.decode('utf-8-sig') or '[]')
            records = records if isinstance(records, list) else [records]
            current_pids = {pid for pid, name in self.windows.processes() if name == 'leagueclientux.exe'}
            for record in records:
                connection = parse_commandline(record.get('ProcessId'), record.get('CommandLine'), current_pids)
                if connection:
                    self.cached = connection
                    return connection
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            pass
        self.cached = None
        # 国服可能保留空 lockfile；此时普通进程不能读取管理员客户端的启动参数。
        self.needs_admin=not is_elevated() and any(process_elevated(self.windows,pid) is True for pid,_ in processes)
        self.reason = ('客户端以管理员权限运行，本工具权限较低，连接参数无法读取。请点击“以管理员权限重新启动”；新窗口先暂停并只读检查。'
            if self.needs_admin else '客户端已运行，但连接参数暂不可读；请等待登录完成后点击“读取当前房间（只读）”。若仍失败，请检查客户端权限或重启客户端。')
        return None


class LCUClient:
    def __init__(self, connection):
        if not isinstance(connection, Connection) or not (0 < connection.port <= 65535) or not connection.token or '\n' in connection.token or '\r' in connection.token:
            raise LCUError('本地客户端连接参数无效')
        self.connection = connection
        self.key = connection.key
        self.capabilities = set()
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.authorization = 'Basic ' + base64.b64encode(('riot:' + connection.token).encode()).decode()

    def request(self, method, path, timeout=0.7):
        reads = {PHASE, SESSION, LOBBY, READY, SELF, '/help'}
        help_route = path.startswith('/help?target=') and path.split('=', 1)[1] in {item[0] for item in OPERATIONS.values()}
        writes = path in {PLAY_AGAIN, ACCEPT} or bool(re.fullmatch(r'/lol-lobby/v[23]/lobby/members/[A-Za-z0-9_-]+/promote', path))
        if (method != 'GET' or (path not in reads and not help_route)) and (method != 'POST' or not writes):
            raise LCUError('请求不在大厅自动化允许列表中')
        request = urllib.request.Request(f'https://127.0.0.1:{self.connection.port}' + path,
            headers={'Authorization': self.authorization, 'Accept': 'application/json'},
            data=b'' if method == 'POST' else None, method=method)
        try:
            with self.http.open(request, timeout=timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise LCUError('客户端响应超出限制', uncertain=method == 'POST')
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise LCUError('客户端请求未完成', exc.code, uncertain=method == 'POST' and exc.code >= 500) from None
        except (OSError, ValueError) as exc:
            # 不保留原始异常文本，避免底层响应/认证参数进入日志。
            raise LCUError('连接超时或响应无效', uncertain=method == 'POST') from None

    def optional(self, path):
        try:
            return self.request('GET', path)
        except LCUError as exc:
            if exc.status == 404:
                return None
            raise

    def inspect(self, cancelled=lambda:False):
        self.capabilities.clear()
        functions = self.request('GET', '/help', timeout=2)
        functions = functions.get('functions', {}) if isinstance(functions, dict) else {}
        for name, (function, route) in OPERATIONS.items():
            if cancelled(): break
            if function not in functions:
                continue
            definition = self.request('GET', '/help?target=' + function, timeout=2)
            definition = definition.get(function, {}) if isinstance(definition, dict) else {}
            arguments = definition.get('arguments')
            expected = [] if name in {'return', 'accept'} else ['puuid' if name == 'promote_v3' else 'summonerId']
            if (definition.get('url') == route and definition.get('http_method') == 'POST'
                    and isinstance(arguments, list) and [next(iter(arg), '') for arg in arguments] == expected):
                self.capabilities.add(name)
        return self.capabilities

    def snapshot(self):
        phase = self.request('GET', PHASE)
        if not isinstance(phase, str):
            raise LCUError('流程阶段格式不受支持')
        identity = self.request('GET', SELF)
        if not isinstance(identity, dict):
            raise LCUError('当前账号尚未就绪')
        account = stable_id(identity)
        if not account:
            raise LCUError('当前账号缺少稳定标识')
        session = self.optional(SESSION) if phase in {'ReadyCheck', 'InProgress', 'WaitingForStats', 'PreEndOfGame', 'EndOfGame'} else None
        lobby = self.optional(LOBBY) if phase == 'Lobby' else None
        ready = self.optional(READY) if phase == 'ReadyCheck' else None
        if any(value is not None and not isinstance(value,dict) for value in [session,lobby,ready]):
            raise LCUError('客户端状态格式不受支持，暂停操作')
        if self.request('GET', PHASE) != phase:
            raise LCUError('流程正在变化，重新读取状态')
        return ClientSnapshot((self.key, account), phase, session, lobby, ready)

    def write(self, operation, member=None):
        if operation not in self.capabilities:
            raise LCUError('当前客户端尚未确认支持此操作')
        route = OPERATIONS[operation][1]
        if operation.startswith('promote'):
            if not isinstance(member, Member) or not member.eligible:
                raise LCUError('接任队友未经过验证')
            value = member.puuid if operation == 'promote_v3' else str(member.summoner_id)
            if operation == 'promote_v3' and not re.fullmatch(r'[A-Za-z0-9_-]{8,160}', value):
                raise LCUError('接任队友标识无效')
            if operation == 'promote_v2' and not positive_id(member.summoner_id):
                raise LCUError('接任队友标识无效')
            route = route.replace('{puuid}', urllib.parse.quote(value, safe='')).replace('{summonerId}', value)
        return self.request('POST', route)


def positive_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isdigit():
        return None
    number = int(value)
    return number if 0 < number < 2 ** 64 else None


def stable_id(data):
    puuid = data.get('puuid')
    if isinstance(puuid, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,160}', puuid):
        return 'puuid:' + puuid
    number = positive_id(data.get('summonerId'))
    return f'summoner:{number}' if number else ''


@dataclass(frozen=True)
class Member:
    key: str = field(repr=False)
    puuid: str = field(repr=False)
    summoner_id: int | None = field(repr=False)
    name: str
    leader: bool
    eligible: bool


@dataclass(frozen=True)
class Lobby:
    party: str = field(repr=False)
    local: str = field(repr=False)
    members: tuple[Member, ...]

    @property
    def me(self):
        return next((m for m in self.members if m.key == self.local), None)


def parse_lobby(data, account):
    if not isinstance(data, dict) or not isinstance(data.get('partyId'), str) or not data['partyId']:
        raise LCUError('大厅缺少会话标识，暂停转让')
    raw = data.get('members')
    local = data.get('localMember')
    if not isinstance(raw, list) or not isinstance(local, dict) or stable_id(local) != account:
        raise LCUError('大厅本人标识不一致，暂停转让')
    members = []
    for item in raw:
        if not isinstance(item, dict):
            raise LCUError('大厅成员格式不受支持')
        key = stable_id(item)
        if not key and item.get('isBot') is True:
            continue
        if not key or any(type(item.get(name)) is not bool for name in ('isLeader', 'isSpectator')) or ('isBot' in item and type(item['isBot']) is not bool):
            raise LCUError('大厅成员身份或接任条件不完整，暂停转让')
        if 'canBeLeader' in item and type(item['canBeLeader']) is not bool:
            raise LCUError('接任权限字段无效，暂停转让')
        # 接口没有普遍适用的“可接任”字段；过滤明确禁止、机器人、观战和无 ID 成员。
        eligible = (not item.get('isBot', False) and not item['isSpectator'] and item.get('canBeLeader') is not False
                    and (key.startswith('puuid:') or positive_id(item.get('summonerId')) is not None))
        name = str(item.get('summonerName') or item.get('gameName') or '未命名队友')
        name = ''.join(char for char in name if char.isprintable())[:80]
        members.append(Member(key, key[6:] if key.startswith('puuid:') else '',
                              positive_id(item.get('summonerId')), name, item['isLeader'], eligible))
    if len({m.key for m in members}) != len(members) or sum(m.leader for m in members) != 1:
        raise LCUError('大厅成员或房主状态不唯一，暂停转让')
    lobby = Lobby(data['partyId'], account, tuple(members))
    if not lobby.me or lobby.me.leader != local.get('isLeader'):
        raise LCUError('本人与大厅房主状态不一致，暂停转让')
    return lobby


@dataclass(frozen=True)
class ClientSnapshot:
    connection: tuple = field(repr=False)
    phase: str
    session: dict | None = field(default=None, repr=False)
    lobby: dict | None = field(default=None, repr=False)
    ready: dict | None = field(default=None, repr=False)

    @property
    def game_id(self):
        data=(self.session or {}).get('gameData', {})
        return positive_id(data.get('gameId')) if isinstance(data,dict) else None
