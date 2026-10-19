"""真实 Windows 恢复组件检查，只操作本脚本创建的 Qt 替身窗口。"""
from pathlib import Path
import json
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lol_words.system import runtime_bootstrap
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QFont,QImage,QPainter
from PySide6.QtWidgets import QApplication,QWidget,QPushButton
from lol_words.system.native import Windows
from lol_words.lobby.client_surface import ClientSurface,Frame


def main():
    native=Windows()
    if native.league_pid():print('SKIP: 对局运行中，未进行窗口检查');return
    app=QApplication([]);window=QWidget();window.setWindowTitle('大厅恢复替身窗口');window.resize(800,500)
    skip=QPushButton('跳过对局结算',window);skip.setGeometry(280,420,240,45)
    avatar=QPushButton('个人头像',window);avatar.setGeometry(680,10,105,45)
    clicks=[];skip.clicked.connect(lambda:clicks.append('skip'))
    window.show();app.processEvents()
    hwnd=int(window.winId())
    row=next(r for r in native.window_inventory() if r['hwnd']==hwnd)
    surface=ClientSurface(native,Path(__file__).resolve().parents[1])
    # 显式替换选窗器以限制到本工具自己的替身；生产选窗器仍只接受 LeagueClientUx。
    surface.window=lambda:row
    def run(function):
        result=[];errors=[]
        def execute():
            try:result.append(function())
            except Exception as exc:errors.append(type(exc).__name__)
        worker=threading.Thread(target=execute);worker.start();deadline=time.monotonic()+12
        while worker.is_alive() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        if worker.is_alive():raise RuntimeError('检查超时')
        if errors:raise RuntimeError(','.join(errors))
        return result[0]
    try:
        native.restore(hwnd,activate=True);app.processEvents()
        evidence=run(lambda:surface.observe(lambda:not native.league_pid(),activate=False))
        assert evidence.skip and evidence.avatar, evidence.reason
        assert not run(lambda:surface.act('skip',evidence,lambda:False))
        assert clicks==[]
        assert run(lambda:surface.act('skip',evidence,lambda:not native.league_pid()))
        deadline=time.monotonic()+1
        while not clicks and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert clicks==['skip']
        image=QImage(900,500,QImage.Format.Format_ARGB32);image.fill(QColor('white'))
        painter=QPainter(image);painter.setPen(QColor('black'));painter.setFont(QFont('Microsoft YaHei',30));painter.drawText(230,440,'跳过对局结算');painter.end()
        frame=Frame(image.width(),image.height(),bytes(image.constBits()))
        ocr=run(lambda:surface.ocr(frame))
        print(json.dumps(dict(native_uia_controls=True,cancel_prevents_click=True,guarded_click_confirmed=True,
            local_ocr_skip_found=bool(ocr),real_client_used=False,writes_to_lcu=0),ensure_ascii=False))
        if not ocr:raise RuntimeError('本机 OCR 未识别替身文本，需要检查语言组件或脚本')
    finally:window.close();app.processEvents()


if __name__=='__main__':main()
