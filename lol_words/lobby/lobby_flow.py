"""大厅基本状态机：接受、转让与正常返回；仅回读确认实际结果。"""
from dataclasses import dataclass, field
import hashlib
import json
import secrets
import time
from lol_words.lobby.lcu_api import LCUError, parse_lobby
from lol_words.lobby.lobby_config import settings
from lol_words.system.state_store import JsonStore

POST_MATCH = {'WaitingForStats', 'PreEndOfGame', 'EndOfGame'}
PHASE_NAMES = {'None':'客户端首页 / 未组队', 'Lobby':'组队房间', 'Matchmaking':'匹配中',
    'ReadyCheck':'等待接受对局', 'ChampSelect':'英雄选择（不自动操作）', 'GameStart':'准备加载',
    'InProgress':'对局中', 'Reconnect':'等待重连（不自动操作）', 'WaitingForStats':'等待结算加载',
    'PreEndOfGame':'结算前流程', 'EndOfGame':'赛后结算'}


@dataclass(frozen=True)
class LobbyStatus:
    connection: str = '未连接客户端'
    phase: str = '等待客户端'
    action: str = '大厅助手已关闭'
    detail: str = '在大厅助手页面分别开启需要的功能。'
    members: tuple = field(default=(), repr=False)
    capabilities: tuple = ()
    needs_admin: bool = False


@dataclass(frozen=True)
class RequestResult:
    kind: str
    reason: str = ''


class ActionJournal:
    """原子领取动作；仅保存加盐摘要、阶段计数与时间，不保存凭据或玩家名。"""
    def __init__(self, path):
        self.store = JsonStore(path)
        self.cache = None
        self.cached_result = None
        self.claimed = set()

    @staticmethod
    def digest(salt, value):
        return hashlib.sha256((salt+'\0'+json.dumps(value, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()

    def observe(self, snapshot, lobby=None):
        account = snapshot.connection[1]
        category = 'post' if snapshot.phase in POST_MATCH else snapshot.phase
        response = (snapshot.ready or {}).get('playerResponse', '')
        signature = (account, category, snapshot.game_id, response, lobby.party if lobby else '', lobby.me.leader if lobby else False, snapshot.connection[0])
        if signature == self.cache:
            return self.cached_result
        def update(data):
            salt = data.setdefault('salt', secrets.token_hex(24))
            key = self.digest(salt, account)
            client_key=self.digest(salt,snapshot.connection[0])
            states = data.setdefault('states', {})
            previous = states.get(key, {})
            ready = previous.get('ready', 0)
            post = previous.get('post', 0)
            tenure = previous.get('tenure', 0)
            game = self.digest(salt, snapshot.game_id) if snapshot.game_id else ''
            party = self.digest(salt, lobby.party) if lobby else ''
            if category == 'ReadyCheck' and (previous.get('phase') != category or previous.get('client')!=client_key or
                    (previous.get('response') == 'Accepted' and response == 'None')):
                ready += 1
            if category == 'post' and (previous.get('phase') != category or (game and previous.get('game') and previous.get('game') != game)):
                post += 1
            leader = bool(lobby and lobby.me.leader)
            if leader and (previous.get('party') != party or not previous.get('leader')):
                tenure += 1
            remembered_game=game or (previous.get('game','') if category=='post' else '')
            states[key] = dict(phase=category, response=response, ready=ready, post=post, tenure=tenure,
                               game=remembered_game, party=party, leader=leader,client=client_key)
            # 多账号长期使用也只保留有限的摘要状态。
            while len(states) > 16:
                del states[next(iter(states))]
            return (key, ready, str(post), party, tenure)
        result = self.store.transact(update)
        self.cache, self.cached_result = signature, result
        return result

    def claim(self, value, operation):
        cache_key=json.dumps([value,operation],sort_keys=True)
        if cache_key in self.claimed:return False
        def update(data):
            salt = data.setdefault('salt', secrets.token_hex(24))
            claims = data.setdefault('claims', {})
            key = self.digest(salt, [value, operation])
            if key in claims:
                return False
            now = time.time()
            for old in list(claims):
                if now - claims[old] > 7*86400:
                    del claims[old]
            while len(claims) >= 256:
                del claims[next(iter(claims))]
            claims[key] = now
            return True
        result=self.store.transact(update)
        self.claimed.add(cache_key)
        return result

    def has_claim(self,value,operation):
        cache_key=json.dumps([value,operation],sort_keys=True)
        if cache_key in self.claimed:return True
        data=self.store.read()
        if not data.get('salt'):return False
        key=self.digest(data['salt'],[value,operation])
        if key in data.get('claims',{}):
            self.claimed.add(cache_key);return True
        return False


class LobbyFlow:
    def __init__(self, journal, report, publish):
        self.journal, self.report, self.publish = journal, report, publish
        self.last_log = None
        self.phase = self.identity = None
        self.phase_since = self.absent_since = None
        self.pending = {}
        self.results = {}
        self.last_status = None

    def log(self, text):
        if text != self.last_log:
            self.last_log = text
            self.report('大厅助手：'+text)

    def status(self, sample=None, action='等待', detail='', members=(), capabilities=(), connection='客户端已连接'):
        value = LobbyStatus(connection, PHASE_NAMES.get(sample.phase, sample.phase) if sample else '等待客户端',
                            action, detail, tuple(members), tuple(sorted(capabilities)))
        if value != self.last_status:
            self.last_status = value
            self.publish(value)

    def cancel(self):
        self.phase = self.identity = None
        self.phase_since = self.absent_since = None
        self.pending.clear()

    @staticmethod
    def current(fresh, expected):
        return (fresh.connection == expected.connection and fresh.phase == expected.phase
                and (not expected.game_id or fresh.game_id == expected.game_id))

    def step(self, client, sample, config, now, game_running, stopped=lambda:False, game_check=lambda:False):
        """stopped 同时包含暂停、退出和设置版本变化；写入前再次读取状态。"""
        cfg = settings(config)
        if stopped(): return
        if self.identity != sample.connection:
            self.cancel(); self.identity = sample.connection
        if game_running:
            self.absent_since = None
        elif self.absent_since is None:
            self.absent_since = now
        if sample.phase != self.phase:
            self.phase, self.phase_since = sample.phase, now
        lobby = parse_lobby(sample.lobby, sample.connection[1]) if sample.phase == 'Lobby' else None
        episode = self.journal.observe(sample, lobby)
        members = tuple((m.key, m.name, m.leader, m.eligible and m.key!=lobby.local) for m in lobby.members) if lobby else ()
        caps = client.capabilities

        def fresh(predicate=lambda s:True):
            if stopped() or game_check(): return False
            latest = client.snapshot()
            return not stopped() and not game_check() and self.current(latest, sample) and predicate(latest)

        def write(operation, scope, predicate=lambda s:True, member=None):
            if operation not in caps:
                return RequestResult('not_sent','当前客户端未确认支持此操作，请手动处理。')
            canonical='promote' if operation.startswith('promote') else operation
            key=json.dumps([scope,canonical],sort_keys=True)
            if key in self.results:return self.results[key]
            if self.journal.has_claim(scope,canonical):
                return RequestResult('unknown','本周期已有尝试记录，结果未知；不自动重复提交。')
            if not fresh(predicate) or stopped():
                return RequestResult('not_sent','执行前客户端阶段或成员状态已变化，本次动作取消。')
            if not self.journal.claim(scope, canonical):
                return RequestResult('unknown','动作已由其他实例领取，不重复提交。')
            if stopped():return RequestResult('not_sent','已暂停，本次动作取消；记录保留以避免重复。')
            try:
                client.write(operation, member)
                result=RequestResult('submitted','请求已提交，等待客户端回读确认；尚未确认成功。')
                self.pending[operation] = (scope, now, member.key if member else None)
                self.log({'return':'已请求返回组队房间，等待回读确认。','accept':'已请求接受对局，等待回读确认。',
                          }.get(operation,'已请求转让队长，等待回读确认。'))
            except LCUError as exc:
                result=RequestResult('unknown' if exc.uncertain else 'rejected',str(exc)+'；本周期不自动重发。')
                if exc.uncertain:self.pending[operation]=(scope,now,member.key if member else None)
                self.log('请求结果不确定，仅回读状态，不反复提交。' if exc.uncertain else '请求被拒绝：'+str(exc))
            self.results[key]=result
            while len(self.results)>256:del self.results[next(iter(self.results))]
            return result

        def show_result(result,verb,submitted):
            action={'submitted':submitted,'rejected':verb+'请求被拒绝','unknown':verb+'结果不确定','not_sent':'未执行'+verb}[result.kind]
            self.status(sample,action,result.reason,members,caps)

        if sample.phase != 'ReadyCheck':
            if self.pending.pop('accept', None):
                self.log('已确认接受对局：客户端已进入英雄选择。' if sample.phase=='ChampSelect' else '接受流程已结束；以客户端实际阶段为准。')
        elif (sample.ready or {}).get('playerResponse') == 'Accepted':
            if self.pending.pop('accept', None): self.log('已确认接受对局。')
        if sample.phase not in POST_MATCH:
            if self.pending.pop('return', None):
                self.log('已确认返回组队房间。' if sample.phase == 'Lobby' else '已离开赛后流程，客户端处于未组队或其他阶段。')
        if lobby and not lobby.me.leader:
            for operation in ['promote_v2','promote_v3']:
                pending=self.pending.pop(operation,None)
                if pending and any(m.key==pending[2] and m.leader for m in lobby.members):
                    self.log('已回读确认选定队友成为队长，本人不再是队长。')
                elif pending:self.log('队长状态已变化，但并非选定队友；不将其记为自动转让成功。')
        if not lobby:
            for operation in ['promote_v2','promote_v3']:self.pending.pop(operation,None)

        if game_running or sample.phase in {'InProgress','Reconnect','GameStart','ChampSelect'}:
            self.status(sample, '保留手动操作', '对局、重连和英雄选择期间不执行大厅动作。', members, caps)
            return
        if sample.phase == 'ReadyCheck' and cfg['lobby_auto_accept']:
            ready = sample.ready or {}
            if ready.get('state') != 'InProgress' or ready.get('playerResponse') != 'None' or ready.get('suppressUx') is True:
                self.status(sample, '本次不自动接受', '已响应、邀请已失效或客户端未展示接受提示。', members, caps)
                return
            if now-self.phase_since < cfg['lobby_accept_delay']:
                self.status(sample, '等待接受延迟', f"等待 {cfg['lobby_accept_delay']:g} 秒，期间可手动拒绝或暂停。", members, caps)
                return
            result=write('accept', episode[:2], lambda s: (s.ready or {}).get('state')=='InProgress' and (s.ready or {}).get('playerResponse')=='None' and not (s.ready or {}).get('suppressUx'))
            show_result(result,'接受对局','接受状态等待确认')
            return
        if lobby and cfg['lobby_auto_promote']:
            if not lobby.me.leader:
                self.status(sample, '当前无需转让', '你不是队长，等待队长开始匹配。', members, caps); return
            candidates = [m for m in lobby.members if m.key != lobby.local and m.eligible and not m.leader]
            preferred = cfg['lobby_preferred_member']
            target = next((m for m in candidates if not preferred or m.key == preferred), None)
            if not target:
                self.status(sample, '等待接任队友', '指定队友未在房间或没有其他可接任成员；不会转给其他人。' if preferred else '房间内暂无其他可接任队友。', members, caps); return
            if now-self.phase_since < 1:
                self.status(sample, '等待大厅稳定', '确认本人队长身份与接任成员后再转让。', members, caps); return
            operation = 'promote_v3' if target.puuid and 'promote_v3' in caps else 'promote_v2'
            def same_member(s):
                current_lobby = parse_lobby(s.lobby, s.connection[1])
                return current_lobby.party == lobby.party and current_lobby.me.leader and any(m.key==target.key and m.eligible and not m.leader for m in current_lobby.members)
            result=write(operation, [episode[0],episode[3],episode[4]], same_member, target)
            show_result(result,'转让队长','转让状态等待确认')
            return
        if sample.phase not in POST_MATCH or not cfg['lobby_auto_return']:
            self.status(sample, '等待客户端阶段', '三个基本功能独立运行；不点击头像，不自动跳过结算。', members, caps)
            return
        if sample.phase!='EndOfGame':
            self.status(sample,'等待正常结算完成','尚未进入完整赛后结算；不提前提交返回请求，不自动跳过结算。',members,caps)
            return
        if self.absent_since is None or now-self.absent_since < 2:
            self.status(sample, '等待游戏进程退出', '赛后仍有游戏进程时不点击客户端。', members, caps); return
        if now-self.phase_since < cfg['lobby_return_delay']:
            self.status(sample, '等待结算流程', f"赛后等待 {cfg['lobby_return_delay']:g} 秒。", members, caps); return
        scope = [episode[0],episode[2]]
        show_result(write('return', [*scope,sample.phase]),'返回房间','正在返回房间')
