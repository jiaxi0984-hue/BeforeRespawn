"""双击打开桌面界面，启动错误使用 Windows 提示框。"""
import ctypes
import traceback
from pathlib import Path
import sys

ROOT = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
if not getattr(sys,'frozen',False):sys.path.insert(0, str(ROOT))

try:
    from lol_words.application.app import main
    raise SystemExit(main())
except Exception:
    path = ROOT / 'startup-error.log'
    try:
        path.write_text(traceback.format_exc(), encoding='utf-8')
    except OSError:
        pass
    if '--check' not in sys.argv:
        message=('程序未能启动，请完整解压程序目录后重试。' if getattr(sys,'frozen',False) else '程序未能启动，请先运行 install.cmd 安装依赖。')
        ctypes.windll.user32.MessageBoxW(None,message+'详细原因已写入 startup-error.log。','LOL Words · 启动失败',0x10)
    raise SystemExit(1)
