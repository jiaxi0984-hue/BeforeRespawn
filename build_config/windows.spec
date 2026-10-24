# -*- mode: python ; coding: utf-8 -*-
"""只构建当前桌面程序，不收集归档、测试、个人配置或运行资料。"""
from pathlib import Path
import sys
import os
ROOT=Path(SPECPATH).resolve().parent
sys.path.insert(0,str(ROOT/'_deps'))
# Do not resolve DLLs from unrelated tools on the developer's PATH (e.g. Poppler ICU).
# Qt 6.12 uses the Windows ICU ABI, not an arbitrary third-party ICU build.
windows=Path(os.environ['SystemRoot'])
os.environ['PATH']=os.pathsep.join(map(str,[Path(sys.executable).parent,Path(sys.base_prefix),windows/'System32',windows]))
from PyInstaller.utils.hooks import collect_submodules,copy_metadata

metadata=[]
for name in ['dxcam','numpy','websocket-client','comtypes','PySide6-Essentials','shiboken6']:
    metadata+=copy_metadata(name)
a=Analysis([str(ROOT/'launchers/start.pyw')],pathex=[str(ROOT),str(ROOT/'_deps')],
    binaries=[],datas=metadata,hiddenimports=collect_submodules('dxcam')+['websocket'],
    hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['tkinter','lol_words.ui.legacy_app','pytest','unittest','PyQt5','PyQt6','PySide2'],
    noarchive=False,optimize=1)
# ICU is supplied by supported Windows 10/11; never redistribute a PATH copy.
a.binaries=[item for item in a.binaries if Path(item[0]).name.lower() not in {'icuuc.dll','icuin.dll'}]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='BeforeRespawn',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=os.environ.get('LOL_WORDS_BUILD_CONSOLE')=='1',
    disable_windowed_traceback=False,uac_admin=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='BeforeRespawn')
