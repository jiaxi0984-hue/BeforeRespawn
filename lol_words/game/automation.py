"""只管理对局生命周期，目标软件由适配器管理。"""

class MatchTracker:
    """接口短暂失联不等于对局结束；GameEnd 或游戏进程消失才结束。"""
    def __init__(self, exit_grace=3.0):
        self.exit_grace = exit_grace
        self.pid = None
        self.active = False
        self.blocked = False
        self.absent_since = None
        self.last_time = None

    def update(self, pid, ready, ended, now, game_time=None):
        actions = []
        if pid is None:
            if self.absent_since is None:
                self.absent_since = now
            if now - self.absent_since >= self.exit_grace:
                if self.active:
                    actions.append("exit")
                self.active = False
                self.pid = self.last_time = None
                self.blocked = False
            return actions
        self.absent_since = None
        reset_time = (ready and game_time is not None and self.last_time is not None
                      and game_time + 5 < self.last_time)
        if self.pid != pid or reset_time:
            if self.active:
                actions.append("exit")
            self.active = False
            self.blocked = False
            self.pid = pid
            self.last_time = None
        if ready and game_time is not None:
            self.last_time = game_time
        if ended:
            self.blocked = True
            if self.active:
                actions.append("exit")
            self.active = False
        elif ready and not self.blocked and not self.active:
            self.active = True
            actions.append("enter")
        return actions
