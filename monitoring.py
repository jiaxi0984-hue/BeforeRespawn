"""唯一的监测编排入口；旧界面回归基线与 Qt 服务共用。"""
import time
from contextlib import nullcontext
from automation import MatchTracker
from mode_runtime import ModeRuntime
from official_download import OfficialDownload
from ui_state import RuntimeState, flow_status


TARGET_STATES = {'ready': '窗口已唯一识别', 'idle': '未运行 · 等待首次需要',
                 'starting': '启动中', 'waiting': '等待唯一窗口',
                 'closed': '已关闭 · 可手动重新打开', 'failed': '启动失败'}


def run_monitor(host, config, stop, root, source_type, capture_type):
    report = lambda text: host.events.put(('log', text))
    tracker = MatchTracker()
    runtime = ModeRuntime(host.windows, root, config, host.ledger, report, host.log_return_result, host.registry)
    runtime.download_factory = lambda report: OfficialDownload(report, host.confirm_download)
    runtime.stopped = stop.is_set
    host.runtime = runtime
    source, source_pid, process_key, capture, last_error = source_type(), None, None, None, ''
    last_poll = float('-inf')

    def publish(game, phase, reason, timer=None, degraded=False):
        # 兼容旧界面测试；结构化状态只发送给新服务。
        if hasattr(host, 'publish_state'):
            m = runtime.manager
            p = runtime.presentation
            missing = (m.adapter.spec['id'] in getattr(host, 'availability', {})
                       and not host.availability[m.adapter.spec['id']] and m.state == 'idle'
                       and m.adapter.spec.get('adapter') != 'web')
            host.publish_state(RuntimeState(
                monitoring='接口暂不可用' if degraded else '正在监测', game=game, phase=phase,
                reason=reason, respawn=timer, target_name=m.adapter.spec['name'],
                target_state='未检测到安装程序' if missing else TARGET_STATES.get(m.state, '正在检查'),
                target_reason='未检测到启动文件，请检测学习软件或通过文件选择器添加。' if missing else (m.last_failure if m.state == 'failed' else m.adapter.reason),
                switch_result=p.last_reason if p.owned else (host.last_return or '尚未切换'),
                pin=p.last_pin_status if p.owned and p.last_pin_status else '未施加临时置顶'))

    try:
        capture = capture_type()
        while not stop.is_set():
            with getattr(host, 'operation_lock', nullcontext()):
                now = time.monotonic()
                runtime.poll_return(now)
                runtime.switch(host.config)
                if now - last_poll >= 1.2:
                    runtime.manager.poll()
                    last_poll = now
                pid = host.windows.league_pid()
                if pid is not None and pid != source_pid:
                    source, source_pid = source_type(), pid
                    identity = host.windows.process_identity(pid)
                    process_key = f"{pid}:{identity['created']}" if identity else str(pid)
                ready, ended, game_time = False, False, None
                if pid is not None:
                    try:
                        game_time, ended = source.status()
                        ready = True
                    except Exception as exc:
                        message = f'等待对局接口：{type(exc).__name__}'
                        if message != last_error:
                            report(message)
                            last_error = message
                for action in tracker.update(pid, ready, ended, now, game_time):
                    if action == 'exit':
                        runtime.end_match()
                    else:
                        runtime.cleanup()
                        runtime.flow = runtime.make_flow(runtime.config)
                        report('已进入实际对局，目标首次安全切出时启动')
                if tracker.active and ready:
                    try:
                        name, sample = source.read(config.get('player_id', ''))
                        if stop.is_set():
                            break
                        runtime.step(name, sample, time.monotonic(), game_time, process_key, time.time(), capture)
                        phase, reason = flow_status(runtime, sample, time.monotonic())
                        publish('对局中', phase, reason, sample.timer if sample.dead else None)
                        host.events.put(('status', f'对局中 · {phase} · {reason}'))
                        last_error = ''
                    except Exception as exc:
                        runtime.data_lost()
                        message = f'本人状态暂不可用：{type(exc).__name__}'
                        if message != last_error:
                            report(message)
                            last_error = message
                        publish('对局中', '等待接口恢复', '本人状态暂不可用，过期切出已取消；等待恢复后下一次完整死亡。', degraded=True)
                        host.events.put(('status', '数据暂不可用，过期切出已取消，等待恢复'))
                elif tracker.active:
                    runtime.data_lost()
                    publish('对局中', '等待接口恢复', '对局接口暂不可用；过期切出已取消，目标保留。', degraded=True)
                    host.events.put(('status', '对局接口暂不可用；目标保留，过期切出已取消'))
                else:
                    game = '对局结束' if ended else ('加载中' if pid else '等待游戏')
                    publish(game, '等待对局', '等待下一局 · 已打开的学习软件与专用页面会保留。')
                    host.events.put(('status', '等待下一局 · 学习软件和专用娱乐页面均保留'))
            stop.wait(config['poll_seconds'] if pid or tracker.active else .6)
    except Exception as exc:
        report(f'监测异常：{type(exc).__name__}；请暂停后继续，完整原因可在本机诊断中检查。')
        publish('未知', '监测已中断', '监测线程遇到异常，请暂停后继续。', degraded=True)
    finally:
        runtime.cleanup()
        if capture:
            capture.close()
        host.events.put(('status', '已暂停；目标保留，临时置顶和待执行任务已清理'))
        if hasattr(host, 'publish_state'):
            host.publish_state(RuntimeState(target_name=runtime.primary.adapter.spec['name'],
                target_state=TARGET_STATES.get(runtime.primary.state, '尚未检查'),
                switch_result=host.last_return or '待执行任务已取消'))
        if hasattr(host, 'on_monitor_exit'):
            host.on_monitor_exit()
