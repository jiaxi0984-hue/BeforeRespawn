"""本人单局累计死亡和惩罚记录；与画面和窗口切换无关。"""
import hashlib
import secrets
import threading
from state_store import JsonStore


class DeathLedger:
    def __init__(self, path, report=lambda text: None):
        self.store = JsonStore(path)
        self.report = report
        self.lock = threading.RLock()
        self.current = None
        self.available = False
        self.failed = False
        self.last_input = None

    def observe(self, player, game_time, process_key, deaths, wall_time):
        if self.failed:
            return
        if type(deaths) is not int or deaths < 0:
            self.available = False
            return
        self.available = True
        with self.lock:
            cache_key = (player, str(process_key), deaths)
            if (self.current and self.last_input == cache_key and
                    self.current['game_time'] <= game_time < self.current['game_time'] + 5):
                return
            try:
                def update(data):
                    salt = data.setdefault('salt', secrets.token_hex(16))
                    who = hashlib.sha256((salt + player).encode()).hexdigest()
                    process = hashlib.sha256(str(process_key).encode()).hexdigest()
                    old = data.get('match', {})
                    start = wall_time - game_time
                    same = (not old.get('ended') and old.get('player') == who and old.get('process') == process
                            and (':' in str(process_key) or abs(old.get('start', -1e10) - start) < 15)
                            and game_time + 5 >= old.get('game_time', 0))
                    if not same:
                        old = dict(id=secrets.token_hex(16), player=who, process=process, start=start,
                                   game_time=game_time, deaths=0, claimed=False, status='未触发', ended=False)
                    previous = old['deaths']
                    old['deaths'] = max(previous, deaths)
                    old['game_time'] = game_time
                    data['match'] = old
                    return dict(old), previous, not same
                current, previous, fresh = self.store.transact(update)
                self.current = current
                self.last_input = cache_key
                if fresh:
                    self.report('已识别新对局，死亡惩罚记录按本局独立管理')
                if previous != current['deaths'] or fresh:
                    self.report(f"本人本局累计死亡：{current['deaths']}；使用本人 scores.deaths，不按轮询次数累计")
            except (OSError, ValueError, TypeError, KeyError):
                self.failed = True
                self.report('惩罚记录无法可靠保存或已损坏，本次运行禁用自动惩罚，避免重复下载')

    def eligible(self, mode, enabled):
        return bool(not self.failed and self.available and self.current and mode == 'entertainment'
                    and enabled and self.current['deaths'] >= 20 and not self.current['claimed'])

    def claim(self):
        """在安全切出时先落盘，再产生外部动作；失败时宁可不下载。"""
        with self.lock:
            if not self.current or self.failed:
                return False
            try:
                def update(data):
                    record = data.get('match', {})
                    if record.get('id') != self.current['id']:
                        return None, False
                    if record.get('claimed'):
                        return dict(record), False
                    record.update(claimed=True, status='已触发，等待官网窗口')
                    return dict(record), True
                result, claimed = self.store.transact(update)
                if result:
                    self.current = result
                return claimed
            except (OSError, ValueError, TypeError):
                self.failed = True
                self.report('惩罚记录保存失败，未发起下载')
                return False

    def set_status(self, status, match_id=None):
        with self.lock:
            if not self.current:
                return
            expected = match_id or self.current['id']
            def update(data):
                record = data.get('match', {})
                if record.get('id') == expected:
                    record['status'] = status
                    return dict(record)
            try:
                result = self.store.transact(update)
            except (OSError, ValueError, TypeError):
                self.failed = True
                self.report('惩罚状态保存失败，停止自动重试，请手动查看已保留的官方页面')
                return
            if result and self.current['id'] == expected:
                self.current = result
                self.report('死亡惩罚：' + status)

    def end(self):
        with self.lock:
            if self.current:
                def update(data):
                    if data.get('match', {}).get('id') == self.current['id']:
                        data['match']['ended'] = True
                try:
                    self.store.transact(update)
                except (OSError, ValueError, TypeError):
                    self.failed = True
                    self.report('未能保存本局结束标记；自动惩罚已禁用')
            self.current = None
            self.available = False
            self.last_input = None
