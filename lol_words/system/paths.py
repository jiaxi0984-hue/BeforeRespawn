"""统一项目根目录；模块移动不改变配置、数据及依赖位置。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
