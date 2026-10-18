"""只处理状态转换，不读取游戏内存，也不操作窗口。"""
from dataclasses import dataclass
from typing import Literal
import time


@dataclass(frozen=True)
class Snapshot:
    dead: bool
    timer: float
    champion: str = ""
    deaths: int | None = None


@dataclass(frozen=True)
class FrameStats:
    gray_fraction: float
    color_fraction: float
    usable_fraction: float


def frame_stats(bgra: bytes, tolerance=14) -> FrameStats:
    """只分析内存中的小尺寸采样，不保存截图；全黑采集视为无效。"""
    if not bgra or len(bgra) % 4:
        raise ValueError("无效画面采样")
    usable = gray = 0
    for offset in range(0, len(bgra), 4):
        b, g, r = bgra[offset:offset + 3]
        # 很暗的像素不能证明场景变灰，也可能来自采集失败。
        if (r * 299 + g * 587 + b * 114) < 25000:
            continue
        usable += 1
        if max(r, g, b) - min(r, g, b) <= tolerance:
            gray += 1
    total = len(bgra) // 4
    return FrameStats(gray / usable if usable else 0,
                      (usable - gray) / usable if usable else 0, usable / total)


class GrayGate:
    """用存活画面作参照，死亡期间连续确认黑白场景。"""

    def __init__(self, threshold=0.82, frames=2):
        self.threshold = threshold
        self.frames = frames
        self.reference = None
        self.consecutive = 0
        self.label = "等待采集存活时的彩色画面"

    def observe(self, dead: bool, stats: FrameStats | None) -> bool:
        valid = stats is not None and stats.usable_fraction >= 0.15
        if not dead:
            self.consecutive = 0
            if valid and stats.color_fraction >= 0.18:
                self.reference = stats.color_fraction
            self.label = "彩色参照已就绪" if self.reference is not None else "等待存活彩色画面"
            return False
        if not valid:
            self.consecutive = 0
            self.label = "当前画面不可采集或过暗"
            return False
        if self.reference is None:
            self.consecutive = 0
            self.label = "缺少存活参照，本次不自动切屏"
            return False
        candidate = (stats.gray_fraction >= self.threshold and
                     stats.color_fraction <= self.reference * 0.45)
        self.consecutive = self.consecutive + 1 if candidate else 0
        self.label = f"灰色占比 {stats.gray_fraction:.0%} · 确认 {self.consecutive}/{self.frames}"
        return self.consecutive >= self.frames


class DeathSession:
    """每次死亡最多发出一次切到 Anki 的请求。"""

    def __init__(self, lead=3.0, delay=3.0):
        self.lead = lead
        self.delay = delay
        self.dead_since = None
        self.wait_reason = "等待本人死亡"
        self.previous = None
        self.eligible = False
        self.attempted = False
        self.finished = False
        self.engaged = False

    def update(self, sample: Snapshot,
               foreground: str, gray_confirmed=False,
               manual_return_confirmed=False, now=None) -> str | None:
        now = time.monotonic() if now is None else now
        if self.previous is None:
            # 中途启动且已经死亡时先观察，下一次完整死亡再触发。
            self.previous = sample.dead
            self.finished = sample.dead
            return None

        new_death = sample.dead and not self.previous
        revived = not sample.dead and self.previous
        self.previous = sample.dead

        if revived:
            should_return = self.engaged
            self.engaged = False
            self.finished = True
            self.dead_since = None
            self.wait_reason = "已复活，取消本次切出"
            return "return" if should_return else None
        if not sample.dead:
            return None

        if new_death:
            self.eligible = True
            self.dead_since = now
            self.attempted = False
            self.finished = False
            self.engaged = False

        # 用户主动返回游戏后，本次死亡不再打扰。
        if self.engaged and foreground == "game" and manual_return_confirmed:
            self.engaged = False
            self.finished = True
            return "manual_return"

        if sample.timer <= self.lead or (not self.engaged and sample.timer <= self.lead + 0.5):
            self.finished = True
            self.wait_reason = "接近复活或倒计时为零，取消本次切出"
            should_return = self.engaged
            self.engaged = False
            return "return" if should_return else None

        if not self.finished and self.eligible and not self.attempted:
            if now - self.dead_since < self.delay:
                self.wait_reason = "等待死亡后切出延迟"
            elif gray_confirmed:
                self.attempted = True
                self.wait_reason = "延迟已到且灰屏已确认"
                return "study"
            else:
                self.wait_reason = "等待可操作阶段结束的灰屏证据或 Anki 就绪"
        return None

    def confirm_study(self, success: bool):
        self.engaged = success
        if not success:
            self.finished = True

    def cancel(self, reason):
        self.finished = True
        self.wait_reason = reason
        self.dead_since = None


def own_snapshot(payload: dict, identity: str = "") -> tuple[str, Snapshot]:
    """严格匹配自己的完整标识；匹配不到时停止，不猜测其他玩家。"""
    active = payload.get("activePlayer", {})
    candidates = {str(active.get(key, "")) for key in ("riotId", "summonerName")}
    if identity.strip():
        candidates = {identity.strip()}
    candidates.discard("")
    matches = [player for player in payload.get("allPlayers", [])
               if any(str(player.get(key, "")) in candidates
                      for key in ("riotId", "summonerName"))]
    if len(matches) != 1:
        raise ValueError("无法唯一识别本人，请填写完整玩家标识（名字#编号）")
    player = matches[0]
    if type(player.get("isDead")) is not bool:
        raise ValueError("游戏未返回有效死亡状态，暂停切换")
    timer = float(player.get("respawnTimer", 0))
    if timer < 0 or timer != timer or timer == float("inf"):
        raise ValueError("复活倒计时无效，暂停切换")
    name = str(player.get("riotId") or player.get("summonerName"))
    champion = str(player.get("championName", ""))
    raw = str(player.get("rawChampionName", ""))
    prefix = "game_character_displayname_"
    if raw.startswith(prefix):
        champion = raw[len(prefix):]
    deaths = player.get('scores', {}).get('deaths') if isinstance(player.get('scores'), dict) else None
    if type(deaths) is not int or deaths < 0:
        deaths = None
    return name, Snapshot(player["isDead"], timer, champion, deaths)
