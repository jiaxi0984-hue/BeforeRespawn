"""双击打开桌面界面，启动错误使用 Windows 提示框。"""
import ctypes
import traceback
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from lol_words.application.app import main
    main()
except Exception:
    path = ROOT / 'startup-error.log'
    try:
        path.write_text(traceback.format_exc(), encoding='utf-8')
    except OSError:
        pass
    ctypes.windll.user32.MessageBoxW(None, '程序未能启动。请先运行 install.cmd 安装依赖，再重试。详细原因已写入 startup-error.log。', 'LOL Words · 启动失败', 0x10)
