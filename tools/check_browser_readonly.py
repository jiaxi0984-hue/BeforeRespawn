"""只读核验独立浏览器与原神官网 PC 入口；不点击、不下载、不读取日常浏览器。"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native import Windows
from browser_target import BrowserAdapter, find_browser
from mode_config import GENSHIN_HOME
from official_download import INSPECT_PC


def main():
    root=Path(__file__).resolve().parents[1]
    spec=dict(id='official-readonly-'+uuid.uuid4().hex[:8],name='原神官网只读检查',adapter='web',url=GENSHIN_HOME)
    windows=Windows();adapter=BrowserAdapter(spec,windows,root)
    browser=find_browser()
    if not browser:
        print('SKIP: 没有可用的 Edge 或 Chrome');return
    adapter.profile.mkdir(parents=True,exist_ok=True)
    process=subprocess.Popen([browser,'--headless=new','--no-first-run','--no-default-browser-check',
        '--user-data-dir='+str(adapter.profile.resolve()),'--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1',GENSHIN_HOME],
        creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        identity=windows.process_identity(process.pid)
        if not identity:raise RuntimeError('无法核实只读浏览器身份')
        def update(data):data.clear();data.update(identity=identity)
        adapter.owner.transact(update)
        deadline=time.monotonic()+30
        result=None
        last_error=''
        while time.monotonic()<deadline:
            try:
                result=adapter.evaluate(INSPECT_PC)
                last_error=''
                if result and result.get('state') in {'ready','link'}:break
            except (OSError,ValueError,KeyError) as exc:last_error=type(exc).__name__+': '+str(exc)
            time.sleep(.5)
        else:raise RuntimeError('未核实到官方 PC 入口；未执行任何点击；'+str(result)+'；'+last_error)
        first=adapter.page()['id']
        adapter.evaluate('document.title')
        if adapter.page()['id']!=first:raise RuntimeError('页面标识发生变化，不能确认复用')
        print('PASS: 独立无界面浏览器身份与自有页面复用检查通过')
        print('PASS: 当前官网 PC 入口可识别；类型='+result['state'])
        print('READ ONLY: 未执行下载点击；未获取安装包；不是实际游戏窗口验证')
    finally:
        # 仅结束此脚本新建并持有 Popen 句柄的检查进程。
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:print('检查进程退出较慢；没有操作其他浏览器')


if __name__=='__main__':main()
