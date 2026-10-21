"""赛后客户端恢复：语义控件、文字识别与用户校准头像；不使用固定坐标。"""
from dataclasses import dataclass, field
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import queue
import threading
from lol_words.system.paths import ROOT


@dataclass
class Frame:
    width: int
    height: int
    pixels: bytes = field(repr=False)
    origin: tuple = (0,0)

    def image(self):
        from PySide6.QtGui import QImage
        return QImage(self.pixels,self.width,self.height,self.width*4,QImage.Format.Format_ARGB32).copy()


@dataclass
class Evidence:
    window: dict = field(default_factory=dict,repr=False)
    skip: dict | None = field(default=None,repr=False)
    avatar: dict | None = field(default=None,repr=False)
    frame: Frame | None = field(default=None,repr=False)
    reason: str = ''


def capture_client(native, hwnd):
    """仅采集实际前台客户端；前后台检查夹住采样，不把遮挡画面当证据。"""
    from lol_words.system.capture import GdiCapture, BitmapHeader
    if native.foreground_handle()!=hwnd or native.is_iconic(hwnd): return None
    cap=GdiCapture();u,g=cap.u,cap.g
    rect,origin=W.RECT(),W.POINT()
    if not (u.GetClientRect(hwnd,C.byref(rect)) and u.ClientToScreen(hwnd,C.byref(origin))): return None
    width,height=rect.right,rect.bottom
    if not (500<=width<=4096 and 300<=height<=2600): return None
    screen=u.GetDC(None);memory=bitmap=old=None
    try:
        memory=g.CreateCompatibleDC(screen)
        header=BitmapHeader();header.size=C.sizeof(BitmapHeader);header.width=width;header.height=-height;header.planes=1;header.bits=32
        bits=C.c_void_p();bitmap=g.CreateDIBSection(screen,C.byref(header),0,C.byref(bits),None,0)
        if not memory or not bitmap or not bits:return None
        old=g.SelectObject(memory,bitmap)
        if not old or old==C.c_void_p(-1).value:old=None;return None
        if not g.StretchBlt(memory,0,0,width,height,screen,origin.x,origin.y,width,height,0x00CC0020):return None
        g.GdiFlush();pixels=C.string_at(bits,width*height*4)
        if native.foreground_handle()!=hwnd or native.is_iconic(hwnd):return None
        import numpy as np
        rgb=np.frombuffer(pixels,dtype=np.uint8).reshape(height,width,4)[:,:,:3]
        if float(rgb.mean())<3 or float(rgb.std())<2:return None
        opaque=np.frombuffer(pixels,dtype=np.uint8).reshape(height,width,4).copy();opaque[:,:,3]=255
        return Frame(width,height,opaque.tobytes(),(origin.x,origin.y))
    finally:
        if old:g.SelectObject(memory,old)
        if bitmap:g.DeleteObject(bitmap)
        if memory:g.DeleteDC(memory)
        if screen:u.ReleaseDC(None,screen)


def match_avatar(frame, template, reference):
    """在校准位置附近匹配缩放头像；低相似度或多个独立匹配均拒绝。"""
    import numpy as np
    from PySide6.QtCore import Qt
    width,height=frame.width,frame.height
    scale=width/reference['width']
    if not .6<=scale<=2 or abs(height/reference['height']-scale)>.25:return None
    image=frame.image();tw=max(16,round(template.width()*scale));th=max(16,round(template.height()*scale))
    target=template.scaled(tw,th,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
    def array(im):
        from PySide6.QtGui import QImage
        im=im.convertToFormat(QImage.Format.Format_ARGB32)
        return np.frombuffer(im.constBits(),np.uint8).reshape(im.height(),im.bytesPerLine())[:,:im.width()*4].reshape(im.height(),im.width(),4)[:,:,:3].astype(float)
    patch=array(target)
    if patch.std(axis=(0,1)).mean()<12:return None
    def soften(values):
        # DPI 重采样的半像素偏移会改变锐利边缘；小范围低通后再比较，仍要求高相似度。
        h,w=values.shape[:2];padded=np.pad(values,((1,1),(1,1),(0,0)),mode='edge')
        return sum(padded[dy:dy+h,dx:dx+w] for dy in range(3) for dx in range(3))/9
    inset=2
    patch=soften(patch)[inset:-inset,inset:-inset]
    tw,th=tw-2*inset,th-2*inset
    x,y,r,b=reference['rect'];expected_x=x*width+inset;expected_y=y*height+inset
    left=max(round(width*.65),round(expected_x-width*.06));top=max(0,round(expected_y-height*.04))
    right=min(width,round(expected_x+tw+width*.06));bottom=min(round(height*.3),round(expected_y+th+height*.04))
    search=soften(array(image.copy(left,top,right-left,bottom-top)))
    h,w=search.shape[:2]
    if h<th or w<tw:return None
    shape=(h+th-1,w+tw-1);sse=np.zeros((h-th+1,w-tw+1))
    for channel in range(3):
        plane=search[:,:,channel];part=patch[:,:,channel]
        integral=np.pad((plane*plane).cumsum(0).cumsum(1),((1,0),(1,0)))
        totals=integral[th:,tw:]-integral[:-th,tw:]-integral[th:,:-tw]+integral[:-th,:-tw]
        corr=np.fft.irfft2(np.fft.rfft2(plane,s=shape)*np.fft.rfft2(part[::-1,::-1],s=shape),s=shape)[th-1:h,tw-1:w]
        sse+=totals+(part*part).sum()-2*corr
    score=1-np.sqrt(np.maximum(sse,0)/(tw*th*3))/255
    py,px=np.unravel_index(score.argmax(),score.shape);best=float(score[py,px])
    if best<.96:return None
    other=score.copy();other[max(0,py-th//2):py+th//2+1,max(0,px-tw//2):px+tw//2+1]=-1
    if other.size and best-float(other.max())<.02:return None
    return dict(source='template',rect=[left+int(px)-inset,top+int(py)-inset,left+int(px)+tw+inset,top+int(py)+th+inset])


class ClientSurface:
    def __init__(self, native, root):
        self.native,self.root=native,Path(root)

    def window(self):
        rows=[r for r in self.native.window_inventory() if Path(r['path']).name.casefold() in {'leagueclientux.exe','leagueclientuxrender.exe'}]
        if len(rows)!=1:return None
        return rows[0]

    def valid(self, row):
        pid=W.DWORD();self.native.u.GetWindowThreadProcessId(row['hwnd'],C.byref(pid))
        identity=self.native.process_identity(pid.value)
        return bool(identity and identity['pid']==row['pid'] and identity['created']==row['created'] and
                    str(Path(identity['path']).resolve()).casefold()==str(Path(row['path']).resolve()).casefold())

    def accessible(self, row, item=None, guard=None):
        payload={k:row[k] for k in ['hwnd','pid','created','path']}
        payload.update(operation='invoke' if item else 'observe')
        if item:payload['item']={k:v for k,v in item.items() if k!='source'}
        try:
            executable=Path(sys.executable)
            if executable.name.casefold()=='pythonw.exe':executable=executable.with_name('python.exe')
            command=[str(executable),'-m','lol_words.lobby.client_accessibility']
            if item:
                if guard is None:return {'invoked':False}
                process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW,cwd=ROOT)
                try:
                    process.stdin.write(json.dumps(payload)+'\n');process.stdin.flush()
                    pending=queue.Queue()
                    reader=threading.Thread(target=lambda:pending.put(process.stdout.readline()),daemon=True);reader.start()
                    ready=json.loads(pending.get(timeout=3) or '{}')
                    approved=ready.get('ready') is True and guard()
                    process.stdin.write(json.dumps({'commit':bool(approved)})+'\n');process.stdin.flush()
                    output,_=process.communicate(timeout=2)
                    return json.loads(output or '{}')
                finally:
                    if process.poll() is None:process.kill();process.communicate()
            result=subprocess.run(command,input=json.dumps(payload)+'\n',
                capture_output=True,text=True,encoding='utf-8',timeout=3,creationflags=subprocess.CREATE_NO_WINDOW,cwd=ROOT)
            return json.loads(result.stdout or '{}')
        except (OSError,ValueError,queue.Empty,subprocess.TimeoutExpired):return {}

    def ocr(self, frame):
        area=self.root/'runtime/client-ocr';area.mkdir(parents=True,exist_ok=True)
        path=area/(uuid.uuid4().hex+'.png')
        try:
            frame.image().save(str(path))
            powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-File',str(Path(__file__).with_name('ocr_client.ps1')),'-ImagePath',str(path.resolve())],
                capture_output=True,timeout=4,creationflags=subprocess.CREATE_NO_WINDOW)
            data=json.loads(result.stdout.decode('utf-8-sig') or '{}')
            matches=[]
            for item in data.get('items',[]):
                rect=item.get('rect',[])
                if len(rect)==4 and all(type(v) in (int,float) for v in rect) and 0<=rect[0]<rect[2]<=frame.width and frame.height*.45<rect[1]<rect[3]<=frame.height:
                    matches.append(dict(source='ocr',rect=rect))
            return matches[0] if len(matches)==1 else None
        except (OSError,ValueError,subprocess.TimeoutExpired):return None
        finally:
            if path.is_file():path.unlink()

    def observe(self, guard, activate=False):
        row=self.window()
        if not row or not guard():return Evidence(reason='客户端主窗口无法唯一识别，未进行点击。')
        if self.native.foreground_handle()!=row['hwnd']:
            if not activate:return Evidence(reason='请将客户端置于前台；不会持续抢焦点。')
            self.native.restore(row['hwnd'],activate=True)
            time.sleep(.2)
        if not guard() or not self.valid(row) or self.native.foreground_handle()!=row['hwnd']:
            return Evidence(reason='客户端未处于前台或阶段已变化，取消识别。')
        result=self.accessible(row);items=result.get('items',[])
        skip=[dict(item,source='uia') for item in items if item.get('kind')=='skip']
        avatar=[dict(item,source='uia') for item in items if item.get('kind')=='avatar']
        frame=capture_client(self.native,row['hwnd'])
        skip_item=skip[0] if len(skip)==1 else self.ocr(frame) if frame and not skip else None
        avatar_item=avatar[0] if len(avatar)==1 else None
        if not avatar_item and not avatar and frame:
            from PySide6.QtGui import QImage
            try:
                reference=json.loads((self.root/'runtime/avatar-template.json').read_text(encoding='utf-8'))
                template=QImage(str(self.root/'runtime/avatar-template.png'))
                if not template.isNull():avatar_item=match_avatar(frame,template,reference)
            except (OSError,ValueError,KeyError,TypeError):pass
        return Evidence(row,skip_item,avatar_item,frame,'没有唯一识别到可点击控件；可在大厅助手校准右上角头像。')

    def act(self, kind, evidence, guard):
        item=evidence.skip if kind=='skip' else evidence.avatar
        row=evidence.window
        if not item or not row or not guard() or not self.valid(row) or self.native.foreground_handle()!=row['hwnd']:return False
        if item['source']=='uia':
            return bool(self.accessible(row,item,guard).get('invoked'))
        # 点击前再采一帧对比目标区域，OCR / 模板的旧画面不能直接产生点击。
        current=capture_client(self.native,row['hwnd'])
        old=evidence.frame
        if not current or not old or (current.width,current.height)!=(old.width,old.height):return False
        import numpy as np
        x,y,r,b=map(int,item['rect'])
        if not (0<=x<r<=current.width and 0<=y<b<=current.height):return False
        before=np.frombuffer(old.pixels,np.uint8).reshape(old.height,old.width,4)[y:b,x:r,:3].astype(float)
        after=np.frombuffer(current.pixels,np.uint8).reshape(current.height,current.width,4)[y:b,x:r,:3].astype(float)
        if float(np.abs(before-after).mean())>4 or not guard() or self.native.foreground_handle()!=row['hwnd']:return False
        # 对实际命中的客户端或其子窗口发送一次完整鼠标点击，不在后台盲点。
        u=self.native.u
        point=W.POINT(current.origin[0]+(x+r)//2,current.origin[1]+(y+b)//2)
        u.WindowFromPoint.argtypes=[W.POINT];u.WindowFromPoint.restype=W.HWND
        u.IsChild.argtypes=[W.HWND,W.HWND];u.IsChild.restype=W.BOOL
        hit=u.WindowFromPoint(point)
        if hit!=row['hwnd'] and not u.IsChild(row['hwnd'],hit):return False
        class Mouse(C.Structure):
            _fields_=[('dx',W.LONG),('dy',W.LONG),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
        class Body(C.Union):_fields_=[('mouse',Mouse)]
        class Input(C.Structure):_fields_=[('type',W.DWORD),('body',Body)]
        packets=(Input*2)();packets[0].body.mouse.flags=0x0002;packets[1].body.mouse.flags=0x0004
        u.SendInput.argtypes=[W.UINT,C.POINTER(Input),C.c_int];u.SendInput.restype=W.UINT
        u.SetCursorPos.argtypes=[C.c_int,C.c_int];u.SetCursorPos.restype=W.BOOL
        if not guard() or self.native.foreground_handle()!=row['hwnd'] or not u.SetCursorPos(point.x,point.y):return False
        return u.SendInput(2,packets,C.sizeof(Input))==2

    def calibration_frame(self, guard):
        row=self.window()
        if not row or not guard() or not self.valid(row):raise ValueError('请先打开并登录客户端，在首页或组队房间中定位头像。')
        self.native.restore(row['hwnd'],activate=True);time.sleep(.2)
        frame=capture_client(self.native,row['hwnd'])
        if not frame or not guard() or not self.valid(row):raise ValueError('客户端画面不可用，请将其置于前台后重试。')
        return frame

    def save_avatar(self, frame, rect):
        x,y,r,b=map(int,rect)
        if not (frame.width*.65<=x<r<=frame.width and 0<=y<b<=frame.height*.3 and 20<=r-x<=140 and 20<=b-y<=140):
            raise ValueError('请只框选右上角头像内部，宽高应为 20–140 像素。')
        image=frame.image().copy(x,y,r-x,b-y)
        import numpy as np
        pixels=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.width(),4)[:,:,:3]
        if pixels.std(axis=(0,1)).mean()<12:raise ValueError('框选区域缺少可识别图案，请重新选择头像内部。')
        path=self.root/'runtime';path.mkdir(parents=True,exist_ok=True)
        if not image.save(str(path/'avatar-template.png')):raise ValueError('头像定位未保存，请检查目录写入权限。')
        reference=dict(width=frame.width,height=frame.height,rect=[x/frame.width,y/frame.height,r/frame.width,b/frame.height])
        temporary=path/'avatar-template.tmp';temporary.write_text(json.dumps(reference),encoding='utf-8');temporary.replace(path/'avatar-template.json')
