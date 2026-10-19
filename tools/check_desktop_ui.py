"""Qt 渲染、尺寸与连接检查。空 / 异常为标记清楚的模拟输入，不启动目标。"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_bootstrap
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from desktop_ui import MainWindow
from application_service import ApplicationService
from ui_state import RuntimeState


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);app.setFont(QFont('Microsoft YaHei UI',10))
    area=Path(__file__).resolve().parents[1]/'runtime/ui-check';area.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=area) as folder:
        service=ApplicationService(Path(folder));window=MainWindow(service,autostart=False)
        window.show();app.processEvents()
        def process(seconds=.15):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        def shot(name):
            process();path=args.output/name
            if not window.grab().save(str(path)):raise RuntimeError('截图保存失败')
        try:
            # 有真实对局时不启动监测、不采样、不触碰其他窗口。
            if service.windows.league_pid():
                window.show_notice('真实界面检查：检测到游戏进程，保持暂停，未运行切换。')
            else:
                service.start();process(2.4)
                window.show_notice('真实后端状态 · 使用隔离默认配置；未启动任何学习软件、网页或下载。')
            shot('01-home-real.png')
            service.stop();process(.5)
            window.navigation.setCurrentRow(1);window.tabs.setCurrentIndex(0)
            window.handle_job('scan','ok',[])
            window.scan_status.setText('模拟空结果 · 检测已完成，未发现学习软件。可选择文件添加。')
            shot('02-learning-empty-simulated.png')
            window.tabs.setCurrentIndex(1)
            window.handle_job('search','error','模拟网络异常 · 查询未完成，请检查网络后重试，或使用内置目录。')
            shot('03-search-error-simulated.png')
            window.render_sites(service.catalog.search(),'catalog');window.site_list.setCurrentRow(0)
            window.search_status.setText('真实内置目录 · 无需联网即可按名称查询官方入口。')
            shot('04-learning-catalog.png')
            window.navigation.setCurrentRow(2);shot('05-entertainment.png')
            window.navigation.setCurrentRow(3);shot('06-settings.png')
            window.navigation.setCurrentRow(4);shot('07-logs.png')
            window.navigation.setCurrentRow(0)
            window.show_notice('模拟状态 · 接口异常；只验证界面呈现，不代表真实对局。',True)
            window.apply_state(RuntimeState(monitoring='接口暂不可用',game='对局中',phase='等待接口恢复',reason='本人状态暂不可用，过期切出已取消；恢复后等待下一次完整死亡。',target_name='Anki',target_state='已关闭 · 可手动重新打开',target_reason='目标已被关闭，可手动重新打开。',switch_result='待执行任务已取消'))
            shot('08-home-error-simulated.png')
            window.resize(900,660);shot('09-small-window.png')
            window.resize(1120,780)
            window.show_notice('模拟状态 · 展示死亡延迟与阶段提示，不代表真实游戏。')
            window.apply_state(RuntimeState(monitoring='正在监测',game='对局中',phase='等待切出延迟',reason='死亡后等待 3 秒；同时保留死亡后的可操作阶段保护。',target_name='Anki',target_state='窗口已唯一识别',respawn=18.2))
            shot('10-death-delay-simulated.png')
            window.show_notice('模拟状态 · 展示学习期间与返回提示，不代表真实游戏。')
            window.apply_state(RuntimeState(monitoring='正在监测',game='对局中',phase='学习中',reason='接近复活时自动返回游戏。',target_name='Anki',target_state='窗口已唯一识别',switch_result='目标已显示',pin='持续置顶正常（不改变键盘焦点）',respawn=15.4))
            shot('11-learning-simulated.png')
            results=dict(qt_platform=app.platformName(),device_pixel_ratio=window.devicePixelRatioF(),
                logical_size=[window.width(),window.height()],screenshots=11,
                live_game_verified=False,downloads_performed=False,
                minimum_window=[window.minimumWidth(),window.minimumHeight()])
            (args.output/'ui-check.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(results,ensure_ascii=False))
        finally:
            service.close()
            while not service.settled():process(.05)
            window.timer.stop();window.closing=True;window.close();app.processEvents()


if __name__=='__main__':main()
