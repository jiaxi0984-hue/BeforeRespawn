"""独立客户端线程；网络与赛后识别不会阻塞死亡监测或 Qt 主线程。"""
import threading
import time
from client_surface import ClientSurface
from lcu_api import ClientDiscovery, LCUClient, LCUError, parse_lobby
from lobby_config import enabled, settings
from lobby_flow import ActionJournal, LobbyFlow, LobbyStatus, PHASE_NAMES


class LobbyRuntime:
    def __init__(self, native, root, config_getter, report, publish, client_factory=LCUClient):
        self.native, self.config_getter, self.report, self.publish = native, config_getter, report, publish
        self.discovery, self.client_factory = ClientDiscovery(native), client_factory
        self.client = None
        self.surface = ClientSurface(native, root)
        self.lock = threading.RLock()
        self.last_value = None
        self.last_error = None
        self.flow = LobbyFlow(ActionJournal(root/'runtime/lobby-actions.json'), report, self.status, self.surface)

    def status(self, value):
        if self.last_value != value:
            self.last_value = value
            self.publish(value)

    def connect(self, stop=lambda:False):
        connection = self.discovery.find(time.monotonic())
        if not connection: raise LCUError(self.discovery.reason,needs_admin=self.discovery.needs_admin)
        if not self.client or self.client.key != connection.key:
            candidate = self.client_factory(connection)
            candidate.inspect(cancelled=stop)
            self.client = candidate
            self.flow.cancel()
        return self.client

    def read_only(self):
        with self.lock:
            self.client=None  # 用户主动检查时重新校验能力，不沿用失败或旧版本缓存。
            self.discovery.cached=None;self.discovery.next_query=0
            try:
                client = self.connect()
                sample = client.snapshot()
                lobby = parse_lobby(sample.lobby, sample.connection[1]) if sample.phase=='Lobby' else None
            except LCUError as exc:
                self.connection_error(exc)
                raise
            members = tuple((m.key,m.name,m.leader,m.eligible and m.key!=lobby.local) for m in lobby.members) if lobby else ()
            value = LobbyStatus('客户端已连接', PHASE_NAMES.get(sample.phase,sample.phase), '只读检查完成',
                '没有发送接受、转让或返回请求。', members, tuple(sorted(client.capabilities)))
            self.status(value)
            self.flow.last_status=None
            return value

    def connection_error(self,exc):
        self.status(LobbyStatus('客户端权限不匹配' if exc.needs_admin else '连接或状态暂不可用',
            phase='尚未读取大厅阶段' if exc.needs_admin else '等待连接恢复',
            action='需要管理员权限' if exc.needs_admin else '自动动作已暂停',detail=str(exc),needs_admin=exc.needs_admin))

    def calibrate(self,cancelled=lambda:False):
        with self.lock:
            client = self.connect(cancelled)
            def guard():
                return not cancelled() and not self.native.league_pid() and client.snapshot().phase in {'Lobby','None'}
            return self.surface.calibration_frame(guard)

    def run(self, stop):
        disabled_reported=False
        try:
            while not stop.is_set():
                config = self.config_getter()
                if not enabled(config):
                    if not disabled_reported:
                        self.status(LobbyStatus(action='大厅助手已关闭', detail='三个功能均未开启；不连接或操作客户端。'))
                    disabled_reported=True
                    stop.wait(.8); continue
                disabled_reported=False
                signature = settings(config)
                cancelled = lambda: stop.is_set() or settings(self.config_getter()) != signature
                try:
                    with self.lock:
                        client = self.connect(cancelled)
                        if cancelled(): continue
                        sample = client.snapshot()
                        self.flow.step(client,sample,config,time.monotonic(),bool(self.native.league_pid()),cancelled,
                                       lambda:bool(self.native.league_pid()))
                    self.last_error = None
                except LCUError as exc:
                    self.flow.cancel()
                    message = str(exc)
                    self.connection_error(exc)
                    if message != self.last_error:
                        self.report('大厅助手：'+message); self.last_error=message
                    # 超时、连接断开和认证变化后不能继续沿用旧候选。
                    self.client=None;self.discovery.cached=None
                except (OSError,ValueError,TypeError,KeyError,AttributeError):
                    self.flow.cancel()
                    message='大厅数据或本地动作记录无法校验；未执行新的自动操作。请检查客户端，或备份后修复 runtime/lobby-actions.json。'
                    self.status(LobbyStatus('状态不可用',action='自动动作已暂停',detail=message))
                    if message!=self.last_error:self.report('大厅助手：'+message);self.last_error=message
                stop.wait(.6)
        finally:
            self.flow.cancel()
            self.status(LobbyStatus(action='大厅助手已暂停',detail='计时与待执行动作已取消；保留已提交动作的去重记录。'))
