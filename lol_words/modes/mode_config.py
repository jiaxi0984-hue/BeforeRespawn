"""模式与目标配置；迁移旧配置时保留已有参数。"""
import copy
from pathlib import Path
from urllib.parse import urlsplit

COMMON_DEFAULTS = dict(anki_path='', player_id='', return_before_seconds=1.0, poll_seconds=0.2,
    gray_threshold=0.82, confirmation_frames=2, capture_region=[0.12, 0.12, 0.88, 0.70],
    death_switch_delay=3.0, persistent_anki_topmost=True, game_display_mode='borderless')
WEBSITES = {'bilibili': ('哔哩哔哩', 'https://www.bilibili.com/'),
            'douyin': ('抖音', 'https://www.douyin.com/')}
GENSHIN_HOME = 'https://ys.mihoyo.com/'


def migrate_config(saved=None):
    config = copy.deepcopy(COMMON_DEFAULTS)
    if isinstance(saved, dict):
        config.update(copy.deepcopy(saved))
    config.setdefault('schema_version', 2)
    if not isinstance(saved, dict) or 'persistent_target_topmost' not in saved:
        config['persistent_target_topmost'] = config['persistent_anki_topmost']
    config.setdefault('mode', 'study')
    config.setdefault('study_target', 'anki')
    config.setdefault('entertainment_site', 'bilibili')
    config.pop('death_penalty_enabled', None)  # 旧开关不再控制内置娱乐事件。
    config.setdefault('targets', [dict(id='anki', name='Anki', adapter='anki',
        command=[config['anki_path']] if config['anki_path'] else [], process_path='',
        title_contains='', window_class='', pin=config['persistent_anki_topmost'])])
    if not any(t.get('id') == 'anki' for t in config['targets']):
        config['targets'].insert(0, dict(id='anki', name='Anki', adapter='anki', command=[],
            process_path='', title_contains='', window_class='', pin=True))
    config['schema_version'] = 2
    validate_config(config)
    from lol_words.lobby.lobby_config import migrate
    return migrate(config)


def valid_web_url(url):
    parts = urlsplit(url)
    return parts.scheme == 'https' and bool(parts.hostname) and not parts.username and not parts.password and parts.port in (None, 443)


def validate_target(target):
    if not target.get('id') or not target.get('name'):
        raise ValueError('目标必须有标识和软件名称')
    if type(target.get('pin', True)) is not bool:
        raise ValueError('目标置顶设置必须为布尔值')
    kind = target.get('adapter')
    if kind == 'web':
        if not valid_web_url(target.get('url', '')):
            raise ValueError('网页目标需要有效的 HTTPS 地址，不可包含账号或密码')
    elif kind in {'anki', 'desktop'}:
        command = target.get('command', [])
        if not isinstance(command, list) or any(not isinstance(x, str) for x in command):
            raise ValueError('启动命令必须是参数数组，不通过命令行外壳执行')
        if kind == 'desktop':
            path = target.get('process_path', '')
            if not command or not Path(command[0]).is_absolute() or not Path(path).is_absolute():
                raise ValueError('自定义软件需要完整启动路径和完整进程路径；可通过自动发现或文件选择添加')
            if Path(path).suffix.lower() != '.exe':
                raise ValueError('进程识别需指定实际 exe 的完整路径')
            if Path(path).name.lower() in {'python.exe','pythonw.exe','java.exe','javaw.exe','dotnet.exe','electron.exe'} and not (target.get('title_contains') or target.get('window_class')):
                raise ValueError('共享解释器进程还需提供窗口标题或类名，避免操作其他程序')
    else:
        raise ValueError('未知目标适配器')


def validate_config(config):
    if config['mode'] not in {'study', 'entertainment'} or config['entertainment_site'] not in WEBSITES:
        raise ValueError('模式或娱乐网站设置无效')
    ids = set()
    names = set()
    for target in config['targets']:
        validate_target(target)
        if target['id'] in ids:
            raise ValueError('目标标识不能重复')
        ids.add(target['id'])
        if target['name'].casefold() in names:
            raise ValueError('请为各学习目标使用不同名称')
        names.add(target['name'].casefold())
    if config['study_target'] not in ids:
        raise ValueError('选中的学习软件不存在')


def selected_spec(config):
    if config['mode'] == 'entertainment':
        key = config['entertainment_site']
        name, url = WEBSITES[key]
        return dict(id='entertainment-' + key, name=name, adapter='web', url=url, pin=True)
    return copy.deepcopy(next(t for t in config['targets'] if t['id'] == config['study_target']))


def mode_key(config):
    import json
    return json.dumps([config['mode'], selected_spec(config)], sort_keys=True, ensure_ascii=False)
