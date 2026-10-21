"""大厅自动化独立配置，不包含英雄选择或自动开始匹配。"""
import math

DEFAULTS = dict(lobby_auto_return=False, lobby_auto_promote=False, lobby_auto_accept=False,
    lobby_accept_delay=1.0, lobby_return_delay=3.0, lobby_preferred_member='')


def migrate(config):
    for key in ('lobby_stuck_seconds','lobby_recover_client'):config.pop(key,None)
    for key, value in DEFAULTS.items():
        config.setdefault(key, value)
    return config


def validate(config):
    for key in ['lobby_auto_return', 'lobby_auto_promote', 'lobby_auto_accept']:
        if type(config[key]) is not bool:
            raise ValueError('大厅助手开关必须为布尔值')
    for key, low, high in [('lobby_accept_delay', 0, 10), ('lobby_return_delay', 0, 30)]:
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'大厅助手时间超出范围：{key}，允许 {low}–{high} 秒')
    if not isinstance(config['lobby_preferred_member'], str) or len(config['lobby_preferred_member']) > 180:
        raise ValueError('指定队友标识无效')


def settings(config):
    return {key: config.get(key, value) for key, value in DEFAULTS.items()}


def enabled(config):
    return any(config.get(key, False) for key in ['lobby_auto_return', 'lobby_auto_promote', 'lobby_auto_accept'])
