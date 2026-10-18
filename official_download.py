"""仅操作专用原神官网页面中的当前官方 PC 按钮，一次提交，不执行下载文件。"""
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

# 从官网实际 PC 链接核实的官方跳转入口，不固定安装包版本或二进制地址。
OFFICIAL_PC_ENTRY = 'https://ys-api.mihoyo.com/event/download_porter/link/ys_cn/official/pc_backup319'


class NoDownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def verified_official_entry():
    """只检查官方入口响应头；不跟随重定向、不读取安装包。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoDownloadRedirect())
        with opener.open(OFFICIAL_PC_ENTRY, timeout=3) as response:
            return None
    except urllib.error.HTTPError as exc:
        target = urlsplit(exc.headers.get('Location', ''))
        if (exc.code in (301, 302, 303, 307, 308) and target.scheme == 'https'
                and target.hostname == 'autopatchcn.yuanshen.com' and target.path.lower().endswith('.exe')
                and not target.username and not target.password):
            return OFFICIAL_PC_ENTRY
    except (OSError, ValueError):
        pass
    return None

# 官网公开配置中可核对 @puzzle/ys-download + options.type=pc。
# 动态解析当前配置的节点标识，不固定版本号、安装包链接或屏幕坐标。
INSPECT_PC = r'''(() => {
 if(location.protocol!=='https:' || location.hostname!=='ys.mihoyo.com') return {state:'wrong_site'};
 if(/验证码|人机验证|安全验证|security check/i.test(document.title)) return {state:'blocked'};
 const ids=[]; const seen=new Set();
 function walk(v,depth=0) { if(!v || typeof v!=='object' || seen.has(v)||depth>30)return;
  seen.add(v); if(v.name==='@puzzle/ys-download' && v.options?.type==='pc' && v.enabled!==false)ids.push(v.id);
  for(const value of Object.values(v))walk(value,depth+1); }
 for(const key of Object.keys(window).filter(k=>k.startsWith('PUZZLE_CONFIG_')||k==='PUZZLE_RENDER_CONFIG')) walk(window[key]);
 for(const id of [...new Set(ids)]) {
  const el=document.getElementById(id)||document.querySelector('[data-id="'+CSS.escape(id)+'"]');
  if(el && el.getBoundingClientRect().width>0 && el.getBoundingClientRect().height>0) return {state:'ready',id}; }
 const links=[...document.querySelectorAll('a[href]')].filter(el=>{
  try{const u=new URL(el.href);return u.protocol==='https:' &&
   ((u.hostname==='ys-api.mihoyo.com' && u.pathname.startsWith('/event/download_porter/link/ys_cn/official/pc_')) ||
    ((u.hostname==='mihoyo.com'||u.hostname.endsWith('.mihoyo.com')||u.hostname.endsWith('.yuanshen.com')) && /\.exe$/i.test(u.pathname) && /hyp|launcher|yuanshen|genshin/i.test(u.pathname)));}catch{return false;}});
 const urls=[...new Set(links.map(a=>a.href))];
 if(urls.length===1) return {state:'link',url:urls[0]};
 return {state:'waiting'};
})()'''


def click_expression(entry, attempt_key):
    import json
    value = json.dumps({**entry, 'attempt_key': attempt_key}, ensure_ascii=True)
    return r'''(() => {
 if(location.protocol!=='https:'||location.hostname!=='ys.mihoyo.com')return 'wrong_site';
 const entry=''' + value + r'''; let el;
 if(window.__lolWordsDownloadAttempted===entry.attempt_key)return 'already_attempted';
 if(entry.state==='ready') el=document.getElementById(entry.id)||document.querySelector('[data-id="'+CSS.escape(entry.id)+'"]');
 else if(entry.state==='link') el=[...document.querySelectorAll('a[href]')].find(a=>a.href===entry.url);
 if(!el)return 'entry_unavailable';
 window.__lolWordsDownloadAttempted=entry.attempt_key; el.click();return 'submitted';
})()'''


class OfficialDownload:
    def __init__(self, report):
        self.report = report
        self.thread = None
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def start(self, adapter, ledger):
        if self.thread:
            return
        match_id = ledger.current['id']
        def status(text):
            ledger.set_status(text, match_id)
        def run():
            try:
                if self.cancelled.is_set():
                    status('官网保留；下载动作已取消，不自动重试')
                    return
                # 每局彩蛋强制导航一次官网，避免复用窗口已被用户浏览到别处。
                adapter.navigate_home()
                entry = None
                for _ in range(60):
                    if self.cancelled.is_set():
                        status('官网保留；学习窗口已结束，取消待执行下载，不自动重试')
                        return
                    entry = adapter.evaluate(INSPECT_PC)
                    if entry and entry.get('state') in {'ready', 'link'}:
                        break
                    if entry and entry.get('state') in {'wrong_site', 'blocked'}:
                        raise ValueError('页面已离开原神官网')
                    if self.cancelled.wait(0.5):
                        continue
                else:
                    fallback = verified_official_entry()
                    if fallback and not self.cancelled.is_set():
                        adapter.request_download(fallback)
                        status('已向经校验的官方 Windows 入口发起一次下载；安装程序或启动器由浏览器处理，未验证完成')
                    else:
                        status('官方 PC 入口不可用或网站验证未完成；官网保留，请手动操作，不循环点击')
                    return
                if self.cancelled.is_set():
                    status('官网保留；下载动作已取消，不自动重试')
                    return
                result = adapter.evaluate(click_expression(entry, match_id))
                if result == 'submitted':
                    status('已向官方 Windows 入口提交一次点击；可能下载米哈游启动器；浏览器确认/拦截请手动处理，未验证完成')
                else:
                    status('官方入口未能提交；官网保留，请手动操作，不自动重试')
            except Exception as exc:
                status(f'官网保留；下载入口未完成（{type(exc).__name__}），请手动操作，不自动重试')
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
