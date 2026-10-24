"""Windows 桌面入口；监测与业务位于 application_service / monitoring。"""
from lol_words.system import runtime_bootstrap
import argparse
import sys
import json
from pathlib import Path
from lol_words.system.paths import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只检查 Qt 界面与采样，不启动监测或目标')
    parser.add_argument('--check-report',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--paused', action='store_true', help='启动后保持暂停')
    parser.add_argument('--admin', action='store_true', help='通过 Windows 授权以管理员权限打开，先保持暂停')
    args = parser.parse_args()
    if args.check_report and not args.check:parser.error('--check-report 只能与 --check 一起使用')
    if args.admin:
        from lol_words.system.privileges import is_elevated,restart_elevated
        if not is_elevated():
            try:restart_elevated(ROOT)
            except ValueError as exc:
                print(str(exc));return 1
            return 0
        args.paused=True
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont
    except ImportError as exc:
        if args.check_report:
            args.check_report.parent.mkdir(parents=True,exist_ok=True)
            args.check_report.write_text(json.dumps(dict(ok=False,frozen=bool(getattr(sys,'frozen',False)),
                stage='qt_import',error_type=type(exc).__name__,detail=str(exc)),ensure_ascii=False,indent=2),encoding='utf-8')
        print('桌面依赖未能加载：'+str(exc) if args.check else '缺少桌面依赖，请先运行 install.cmd。')
        if sys.executable.lower().endswith('pythonw.exe'):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '缺少桌面依赖，请先运行 install.cmd，再打开本工具。', 'BeforeRespawn · 缺少依赖', 0x10)
        return 1
    from lol_words.application.application_service import ApplicationService
    from lol_words.ui.desktop_ui import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName('BeforeRespawn')
    app.setOrganizationName('BeforeRespawn')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    service = ApplicationService(ROOT)
    window = MainWindow(service, autostart=not (args.check or args.paused))
    if args.check:
        window.timer.stop()  # 纯界面检查不发起目标发现线程。
    window.show()
    if args.check:
        from lol_words.system.capture import SceneCapture
        app.processEvents()
        capture = SceneCapture()
        result=dict(ok=False,frozen=bool(getattr(sys,'frozen',False)),executable=sys.executable,root=str(ROOT),
                    application_name=app.applicationName(),window_title=window.windowTitle())
        try:
            from lol_words.targets.catalog import LearningCatalog
            if not LearningCatalog().search('Anki') or not (ROOT/'assets/chevron-down.svg').is_file():
                raise RuntimeError('程序资源缺失，请完整解压程序目录')
            pixels = capture.read(int(window.winId()))
            if not pixels:
                raise RuntimeError('界面采样未返回有效数据')
            result.update(ok=True,bytes=len(pixels),backend=capture.backend,pages=window.navigation.count(),resources_valid=True)
            print(f'Qt 界面与采样通过：{len(pixels)} 字节；后端={capture.backend}；未启动监测、目标或下载。')
        except Exception as exc:
            result.update(error_type=type(exc).__name__)
            raise
        finally:
            capture.close()
            service.close()
            window.timer.stop()
            window.closing = True
            window.close()
            app.processEvents()
            if args.check_report:
                args.check_report.parent.mkdir(parents=True,exist_ok=True)
                args.check_report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
