"""有界辅助进程，只返回或调用指定客户端窗口的匹配控件。"""
import json
import sys
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path

SKIP_WORDS = ('跳过对局结算', '跳过结算', '跳过等待', 'skip waiting', 'skip stats')
AVATAR_WORDS = ('头像', '个人资料', 'summoner profile', 'player profile', 'avatar')


def run(request):
    from lol_words.system.native import Windows
    from lol_words.system import runtime_bootstrap
    import comtypes.client
    native = Windows()
    hwnd = int(request['hwnd'])
    def valid():
        pid = W.DWORD(); native.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        identity = native.process_identity(pid.value)
        return bool(identity and identity['pid']==request['pid'] and identity['created']==request['created']
                    and str(Path(identity['path']).resolve()).casefold()==str(Path(request['path']).resolve()).casefold()
                    and native.is_visible(hwnd) and not native.is_iconic(hwnd))
    if not valid(): return {'items': [], 'reason': '客户端窗口身份已变化'}
    module = comtypes.client.GetModule('UIAutomationCore.dll')
    automation = comtypes.client.CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
    root = automation.ElementFromHandle(hwnd)
    area = root.CurrentBoundingRectangle
    nodes = root.FindAll(4, automation.CreateTrueCondition())  # TreeScope_Descendants
    items = []
    for index in range(min(nodes.Length, 1200)):
        node = nodes.GetElement(index)
        try:
            name = str(node.CurrentName or '')
            lower = name.casefold()
            kind = 'skip' if any(word in lower for word in SKIP_WORDS) else 'avatar' if any(word in lower for word in AVATAR_WORDS) else ''
            if not kind or not node.CurrentIsEnabled or node.CurrentIsOffscreen: continue
            rect = node.CurrentBoundingRectangle
            if rect.right<=rect.left or rect.bottom<=rect.top: continue
            x=(rect.left+rect.right)/2; y=(rect.top+rect.bottom)/2
            if kind=='avatar' and not (x>area.left+.65*(area.right-area.left) and y<area.top+.3*(area.bottom-area.top)): continue
            if kind=='skip' and y<area.top+.45*(area.bottom-area.top): continue
            try:
                invoke=node.GetCurrentPattern(10000).QueryInterface(module.IUIAutomationInvokePattern)
            except Exception:
                continue
            item=dict(kind=kind,name=name[:100],automation_id=str(node.CurrentAutomationId or ''),
                rect=[rect.left,rect.top,rect.right,rect.bottom],control_type=int(node.CurrentControlType))
            if request.get('operation')=='invoke':
                expected=request.get('item',{})
                if item!=expected: continue
                items.append((item,invoke))
            else: items.append(item)
        except Exception:
            continue
    if request.get('operation')=='invoke':
        if len(items)!=1 or not valid() or native.foreground_handle()!=hwnd:
            return {'invoked':False}
        # 等待父线程在识别完成后重新核对 LCU 阶段；不把旧画面直接变成点击。
        print(json.dumps({'ready':True}),flush=True)
        try:commit=json.loads(sys.stdin.readline(200))
        except (ValueError,OSError):return {'invoked':False}
        if commit.get('commit') is not True or not valid() or native.league_pid() or native.foreground_handle()!=hwnd:
            return {'invoked':False}
        items[0][1].Invoke()
        return {'invoked':True}
    unique=[]
    for item in items:
        if item not in unique: unique.append(item)
    return {'items':unique}


if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.readline(20000))
        print(json.dumps(run(request),ensure_ascii=False))
    except Exception:
        print(json.dumps({'items':[],'invoked':False,'reason':'客户端未开放可访问控件'}))
