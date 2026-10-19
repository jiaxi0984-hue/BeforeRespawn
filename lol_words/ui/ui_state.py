"""界面只消费这些真实业务状态，不自行推断死亡或切换成功。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeState:
    monitoring: str = '已暂停'
    game: str = '尚未读取'
    phase: str = '已暂停'
    reason: str = '继续监测后等待游戏；已打开的目标会保留。'
    respawn: float | None = None
    target_name: str = ''
    target_state: str = '尚未检查'
    target_reason: str = ''
    switch_result: str = '尚未切换'
    pin: str = '未施加临时置顶'


def flow_status(runtime, sample, now):
    """展示状态机已有判断；不复制状态机的动作决策。"""
    s, p, flow = runtime.flow.session, runtime.presentation, runtime.flow
    if p.returning:
        return '正在返回', '正在确认游戏前台及目标遮挡已消失。'
    if s.engaged:
        return ('学习中' if runtime.config['mode'] == 'study' else '娱乐中'), '接近复活时自动返回游戏。'
    if not sample.dead:
        return '正常游戏', '持续监测本人状态，死亡后再判断是否可以切出。'
    if s.finished:
        return '本次切出已取消', s.wait_reason or '等待下一次完整死亡。'
    if s.dead_since is not None and now - s.dead_since < s.delay:
        return '等待切出延迟', f'死亡后等待 {s.delay:g} 秒；同时保留死亡后可操作阶段保护。'
    if not flow.confirmed:
        return '等待画面确认', '等待死亡后的可操作阶段结束；' + flow.gate.label
    return '等待目标窗口', runtime.manager.adapter.reason or '灰屏已确认，等待目标唯一窗口就绪。'
