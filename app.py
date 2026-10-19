"""Windows 桌面入口；监测与业务位于 application_service / monitoring。"""
import runtime_bootstrap
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只检查 Qt 界面与采样，不启动监测或目标')
    args = parser.parse_args()
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont
    except ImportError:
        print('缺少桌面依赖，请先运行 install.cmd。')
        if sys.executable.lower().endswith('pythonw.exe'):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '缺少桌面依赖，请先运行 install.cmd，再打开本工具。', 'LOL Words · 缺少依赖', 0x10)
        return 1
    from application_service import ApplicationService
    from desktop_ui import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName('LOL Words')
    app.setOrganizationName('LOL-Words-zhandoushuang')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    service = ApplicationService(Path(__file__).resolve().parent)
    window = MainWindow(service, autostart=not args.check)
    if args.check:
        window.timer.stop()  # 纯界面检查不发起目标发现线程。
    window.show()
    if args.check:
        from capture import SceneCapture
        app.processEvents()
        capture = SceneCapture()
        try:
            pixels = capture.read(int(window.winId()))
            if not pixels:
                raise RuntimeError('界面采样未返回有效数据')
            print(f'Qt 界面与采样通过：{len(pixels)} 字节；后端={capture.backend}；未启动监测、目标或下载。')
        finally:
            capture.close()
            service.close()
            window.timer.stop()
            window.closing = True
            window.close()
            app.processEvents()
        return 0
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
