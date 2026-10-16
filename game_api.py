"""英雄联盟本机接口，短暂不可用不代表对局结束。"""
import json
import math
import ssl
import time
import urllib.request
from core import own_snapshot


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("本地游戏接口返回重定向，已停止连接")


class GameData:
    def __init__(self):
        self.http = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.active = None
        self.next_status = 0
        self.last_time = None
        self.ended = False

    def get(self, endpoint):
        request = urllib.request.Request("https://127.0.0.1:2999/liveclientdata/" + endpoint,
                                         headers={"Accept": "application/json"})
        with self.http.open(request, timeout=0.6) as response:
            return json.load(response)

    def status(self):
        now = time.monotonic()
        if now < self.next_status and self.last_time is not None:
            return self.last_time, self.ended
        stats = self.get("gamestats")
        game_time = float(stats["gameTime"])
        if not math.isfinite(game_time) or game_time < 0:
            raise ValueError("无效对局时间")
        if self.last_time is not None and game_time + 5 < self.last_time:
            self.active = None
            self.ended = False
        self.last_time = game_time
        try:
            events = self.get("eventdata").get("Events", [])
            self.ended = self.ended or any(event.get("EventName") == "GameEnd" for event in events)
        except (OSError, ValueError, TypeError, AttributeError):
            # 有的版本暂不提供事件列表，游戏进程退出仍可确认结束。
            pass
        self.next_status = now + 0.8
        return game_time, self.ended

    def read(self, identity=""):
        if self.active is None:
            self.active = self.get("activeplayer")
        players = self.get("playerlist")
        try:
            return own_snapshot({"activePlayer": self.active, "allPlayers": players}, identity)
        except ValueError:
            if identity:
                raise
            # 兼容 activeplayer 未携带完整 Riot 标识的客户端。
            name = self.get("activeplayername")
            if not isinstance(name, str) or not name:
                raise ValueError("暂时无法从客户端获取本人标识")
            self.active = {"riotId": name, "summonerName": name}
            return own_snapshot({"activePlayer": self.active, "allPlayers": players})
