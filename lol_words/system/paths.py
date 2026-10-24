"""统一项目根目录；模块移动不改变配置、数据及依赖位置。"""
from pathlib import Path
import sys

def application_root():
    """便携程序将配置和资源放在 exe 同目录，源码版继续使用项目根目录。"""
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]

ROOT = application_root()
