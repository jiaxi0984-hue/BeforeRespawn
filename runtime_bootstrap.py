"""优先加载项目随附的依赖，不修改系统 Python。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if (ROOT / '_deps').is_dir():
    sys.path.insert(0, str(ROOT / '_deps'))
