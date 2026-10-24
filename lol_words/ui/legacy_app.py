"""英雄联盟死亡时联动学习软件或专用娱乐网站窗口。"""
from __future__ import annotations
import argparse
import copy
import ctypes as C
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
import uuid
from lol_words.game.automation import MatchTracker
from lol_words.system.capture import SceneCapture
from lol_words.game.game_api import GameData
from lol_words.system.native import Windows
from lol_words.targets.adapters import AdapterRegistry
from lol_words.modes.mode_config import migrate_config, validate_config, selected_spec, WEBSITES
from lol_words.modes.mode_runtime import ModeRuntime
from lol_words.modes.penalty import DeathLedger
from lol_words.targets.software_catalog import scan_learning_apps, detected_target
from lol_words.targets.catalog import LearningCatalog, online_candidates
from lol_words.modes.official_download import OfficialDownload

from lol_words.system.paths import ROOT
DEFAULTS = migrate_config()
DISPLAY_MODES = {"自动兼容（全屏 / 无边框）": "auto", "无边框": "borderless", "全屏": "fullscreen"}


class App:
    def __init__(self, root, autostart=True):
        self.root, self.windows = root, Windows()
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = self.finder = None
        self.closing = self.hotkey_down = False
        self._test_active = False
        self.pin_tester = None
        self.runtime = None
        self.registry = AdapterRegistry(self.windows, ROOT, lambda text: self.events.put(('log', text)))
        self.catalog = LearningCatalog(user_path=ROOT/'runtime'/'catalog-overrides.json')
        self.ledger = DeathLedger(ROOT / 'runtime' / 'penalty.json', lambda text: self.events.put(('log', text)))
        settings = ROOT / 'config.json'
        if not settings.exists():
            settings = ROOT / 'config.example.json'
        self.config_error = ''
        try:
            saved = json.loads(settings.read_text(encoding='utf-8-sig'))
            if not isinstance(saved,dict):raise ValueError('配置根节点必须为对象')
            self.config = migrate_config(saved)
        except FileNotFoundError:
            self.config = migrate_config()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.config_error = f'配置文件无效，请修正后重新启动；原文件未覆盖：{exc}'
            self.config = migrate_config()
        self.lead = tk.StringVar(value=str(self.config['return_before_seconds']))
        self.threshold = tk.StringVar(value=str(round(self.config['gray_threshold'] * 100)))
        self.switch_delay = tk.StringVar(value=str(self.config['death_switch_delay']))
        self.persistent_pin = tk.BooleanVar(value=self.config['persistent_target_topmost'])
        self.display_mode = tk.StringVar(value=next((k for k,v in DISPLAY_MODES.items() if v==self.config['game_display_mode']), '无边框'))
        self.mode = tk.StringVar(value=self.config['mode'])
        self.target_choice = tk.StringVar(value=selected_spec({**self.config, 'mode':'study'})['name'])
        self.target_pin = tk.BooleanVar(value=selected_spec({**self.config, 'mode':'study'}).get('pin',True))
        self.site = tk.StringVar(value=WEBSITES[self.config['entertainment_site']][0])
        self.penalty_status = tk.StringVar(value='本局死亡：未知 · 惩罚未触发')
        self.status = tk.StringVar(value='等待游戏 · 首次安全切出时启动目标')
        self.path_label = tk.StringVar(value='自动发现优先，也可通过文件选择添加；多个匹配窗口时不会随意选择')
        self.build()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.tick)
        if self.config_error:
            self.append(self.config_error)
        elif autostart:
            root.after(350, self.start)

    def build(self):
        self.root.title('BeforeRespawn · 学习 / 娱乐')
        self.root.geometry('1040x880')
        self.root.minsize(1000, 800)
        style=ttk.Style()
        style.configure('TLabel', font=('Microsoft YaHei UI',10))
        style.configure('TButton', font=('Microsoft YaHei UI',10), padding=5)
        box=ttk.Frame(self.root,padding=20); box.pack(fill='both',expand=True);box.columnconfigure(0,weight=1)
        ttk.Label(box,text='战斗与学习 · 自选目标',font=('Microsoft YaHei UI',20,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(box,text='死亡 → 等待切出延迟 + 灰屏确认 → 目标窗口 → 复活前返回；对局结束保留目标',wraplength=940).grid(row=1,column=0,sticky='w',pady=8)
        modes=ttk.Frame(box);modes.grid(row=2,column=0,sticky='w',pady=6)
        for label,value in [('学习模式','study'),('娱乐模式','entertainment')]:
            ttk.Radiobutton(modes,text=label,value=value,variable=self.mode,command=self.change_mode).pack(side='left',padx=(0,20))
        self.study_box=ttk.LabelFrame(box,text='学习目标',padding=10)
        self.study_box.grid(row=3,column=0,sticky='ew',pady=4)
        self.target_box=ttk.Combobox(self.study_box,textvariable=self.target_choice,state='readonly',width=25,values=[t['name'] for t in self.config['targets']])
        self.target_box.pack(side='left',padx=(0,8));self.target_box.bind('<<ComboboxSelected>>',self.choose_target)
        for label,command in [('检测学习软件',self.discover_apps),('添加 / 配置桌面软件',self.add_desktop),('搜索学习官网',self.add_web)]:
            ttk.Button(self.study_box,text=label,command=command).pack(side='left',padx=3)
        ttk.Checkbutton(self.study_box,text='此目标允许置顶',variable=self.target_pin,command=self.save).pack(side='left',padx=4)
        self.fun_box=ttk.LabelFrame(box,text='娱乐目标',padding=10)
        self.fun_box.grid(row=3,column=0,sticky='ew',pady=4)
        sites=ttk.Combobox(self.fun_box,textvariable=self.site,state='readonly',width=22,values=[v[0] for v in WEBSITES.values()]);sites.pack(anchor='w')
        sites.bind('<<ComboboxSelected>>',lambda _:self.save())
        ttk.Label(box,textvariable=self.path_label,wraplength=940,foreground='#155e75').grid(row=4,column=0,sticky='w',pady=6)
        options=ttk.Frame(box);options.grid(row=5,column=0,sticky='w',pady=6)
        for text,var,low,high in [('灰色占比至少（%）',self.threshold,50,99),('死亡后切出延迟（秒）',self.switch_delay,0,120),('复活前提前返回（秒）',self.lead,0,30)]:
            ttk.Label(options,text=text).pack(side='left');ttk.Spinbox(options,textvariable=var,from_=low,to=high,width=5).pack(side='left',padx=(6,14))
        display=ttk.Frame(box);display.grid(row=6,column=0,sticky='w',pady=6)
        ttk.Label(display,text='游戏显示方式：').pack(side='left')
        combo=ttk.Combobox(display,textvariable=self.display_mode,values=list(DISPLAY_MODES),state='readonly',width=24);combo.pack(side='left');combo.bind('<<ComboboxSelected>>',lambda _:self.save())
        ttk.Checkbutton(box,text='死亡期间维持目标窗口置顶（同时遵循每个目标自身的置顶设置；不持续抢焦点）',variable=self.persistent_pin).grid(row=7,column=0,sticky='w',pady=6)
        buttons=ttk.Frame(box);buttons.grid(row=8,column=0,sticky='w',pady=8)
        for label,command in [('暂停',self.stop),('恢复监测',self.start),('发现当前目标',self.rediscover),('手动重新打开目标',self.reopen_target),('保存设置',self.save)]:
            ttk.Button(buttons,text=label,command=command).pack(side='left',padx=(0,8))
        ttk.Label(box,text='模式、目标保存后立即切换并取消旧任务；公共计时与置顶参数暂停恢复后生效。\n网页使用独立 Edge/Chrome 窗口；不接入日常浏览器标签页。后台无灰屏证据时短暂恢复游戏采样。',wraplength=940).grid(row=9,column=0,sticky='w',pady=6)
        ttk.Label(box,textvariable=self.status,wraplength=940,foreground='#155e75').grid(row=10,column=0,sticky='w',pady=6)
        self.log=tk.Text(box,state='disabled',height=9,wrap='word',background='#f1f5f9',font=('Microsoft YaHei UI',9));self.log.grid(row=11,column=0,sticky='nsew');box.rowconfigure(11,weight=1)
        self.refresh_mode_visibility()
        self.append('自动监测不读取游戏内存；官方页面或学习软件只在安全切出条件满足时自动打开。')

    def refresh_mode_visibility(self):
        self.study_box.grid_remove();self.fun_box.grid_remove()
        (self.study_box if self.mode.get()=='study' else self.fun_box).grid()

    def change_mode(self):
        self.refresh_mode_visibility()
        self.save()

    def choose_target(self, event=None):
        target=next(t for t in self.config['targets'] if t['name']==self.target_choice.get())
        self.target_pin.set(target.get('pin',True))
        self.save()

    def append(self, text):
        line = time.strftime("%H:%M:%S ") + text + "\n"
        self.log.configure(state="normal")
        self.log.insert("end", line)
        if int(self.log.index("end-1c").split(".")[0]) > 180:
            self.log.delete("1.0", "40.0")
        self.log.see("end")
        self.log.configure(state="disabled")
        try:
            path = ROOT / "runtime.log"
            if path.exists() and path.stat().st_size > 300000:
                path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8") as out:
                out.write(line)
        except OSError:
            pass

    def save(self, report=True):
        if self.config_error:
            self.append(self.config_error)
            return False
        try:
            lead,threshold,delay=float(self.lead.get()),float(self.threshold.get())/100,float(self.switch_delay.get())
            if not(0<=lead<=30 and .5<=threshold<=.99 and 0<=delay<=120):raise ValueError('时间或阈值超出范围')
            config=copy.deepcopy(self.config)
            config.update(return_before_seconds=lead,gray_threshold=threshold,death_switch_delay=delay,
                persistent_anki_topmost=self.persistent_pin.get(),persistent_target_topmost=self.persistent_pin.get(),game_display_mode=DISPLAY_MODES[self.display_mode.get()],
                mode=self.mode.get(),study_target=next(t['id'] for t in config['targets'] if t['name']==self.target_choice.get()),
                entertainment_site=next(k for k,v in WEBSITES.items() if v[0]==self.site.get()))
            config.pop('death_penalty_enabled',None)
            for target in config['targets']:
                if target['id']==config['study_target']:
                    target['pin']=self.target_pin.get()
            validate_config(config)
            temporary=ROOT/'config.tmp';temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(ROOT/'config.json')
            self.config=config
        except (ValueError,KeyError,StopIteration) as exc:
            messagebox.showerror('设置无效',str(exc) or '请核对目标、阈值和时间设置')
            return False
        except OSError as exc:
            self.append(f'设置未能保存：{exc}');return False
        if report:self.append('设置已保存；模式和目标立即切换；公共计时、置顶参数暂停并恢复后生效。')
        return True

    def confirm_download(self,cancelled):
        """下载线程请求明确同意；不阻塞游戏监测，也不持久保存同意。"""
        reply=threading.Event();answer={'approved':False}
        self.events.put(('download_request',(reply,answer,cancelled)))
        deadline=time.monotonic()+30
        while not reply.wait(.1):
            if cancelled.is_set() or self.closing or self.stop_event.is_set() or time.monotonic()>=deadline:
                cancelled.set();return False
        return bool(answer['approved'] and not cancelled.is_set() and not self.closing and not self.stop_event.is_set())

    def show_download_prompt(self,request):
        reply,answer,cancelled=request
        if cancelled.is_set() or self.closing or self.stop_event.is_set():reply.set();return None
        dialog=tk.Toplevel(self.root);dialog.title('官网下载确认');dialog.geometry('520x200');dialog.transient(self.root)
        dialog.attributes('-topmost',True)  # 高于正在置顶的官网；确认或取消后销毁，复活时自动清理。
        ttk.Label(dialog,text='已打开原神官网。是否从官网下载 Windows 客户端安装程序或启动器？\n不会自动运行或安装。',wraplength=480).pack(padx=18,pady=20)
        def finish(approved=False):
            answer['approved']=bool(approved and not cancelled.is_set());reply.set()
            if dialog.winfo_exists():dialog.destroy()
        buttons=ttk.Frame(dialog);buttons.pack(pady=8)
        ttk.Button(buttons,text='确认下载',command=lambda:finish(True)).pack(side='left',padx=8)
        ttk.Button(buttons,text='仅保留官网',command=finish).pack(side='left',padx=8)
        dialog.protocol('WM_DELETE_WINDOW',finish)
        def watch():
            if not dialog.winfo_exists():return
            if cancelled.is_set() or self.closing or self.stop_event.is_set():finish();return
            dialog.after(100,watch)
        dialog.after(100,watch);dialog.lift()
        return dialog

    def current_display_mode(self, fallback):
        mode=getattr(self,'config',{}).get('game_display_mode',fallback)
        return mode if mode in DISPLAY_MODES.values() else fallback

    def selected_manager(self):
        return self.registry.get(selected_spec(self.config))

    def reopen_target(self):
        if not self.save(report=False):return
        manager=self.selected_manager()
        threading.Thread(target=lambda:manager.ensure(manual=True),daemon=True).start()

    def rediscover(self, force=False):
        if self.finder and self.finder.is_alive():return
        spec=selected_spec(self.config);manager=self.registry.get(spec)
        def find():
            try:
                path=manager.adapter.discover()
                self.events.put(('discovery',(spec['id'],path,manager.adapter.configuration_updates(path))))
            except Exception as exc:self.events.put(('log',f'目标发现未完成：{type(exc).__name__}'))
        self.finder=threading.Thread(target=find,daemon=True);self.finder.start()

    def add_desktop(self, preset=None):
        selected=next(t for t in self.config['targets'] if t['id']==self.config['study_target'])
        preset=dict(preset or (selected if selected['adapter']=='desktop' else {}))
        dialog=tk.Toplevel(self.root);dialog.title('自定义桌面软件');dialog.geometry('760x400');dialog.transient(self.root)
        values={key:tk.StringVar(value=value) for key,value in [('name',preset.get('name','')),('command',json.dumps(preset.get('command',[]),ensure_ascii=False)),('process_path',preset.get('process_path','')),('title_contains',preset.get('title_contains','')),('window_class',preset.get('window_class',''))]}
        for row,(key,label) in enumerate([('name','软件名称'),('command','启动命令（参数数组）'),('process_path','精确进程路径'),('title_contains','窗口标题包含（可留空）'),('window_class','窗口类名（可留空）')]):
            ttk.Label(dialog,text=label).grid(row=row,column=0,sticky='w',padx=10,pady=8)
            ttk.Entry(dialog,textvariable=values[key],width=65).grid(row=row,column=1,padx=10)
        def browse():
            path=filedialog.askopenfilename(parent=dialog,title='选择已安装学习软件',filetypes=[('桌面程序','*.exe')])
            if path:
                values['command'].set(json.dumps([path],ensure_ascii=False));values['process_path'].set(path)
                if not values['name'].get():values['name'].set(Path(path).stem)
        ttk.Button(dialog,text='选择程序文件',command=browse).grid(row=5,column=0,padx=10)
        pin=tk.BooleanVar(value=preset.get('pin',True));ttk.Checkbutton(dialog,text='此目标允许死亡期间置顶',variable=pin).grid(row=5,column=1,sticky='w')
        ttk.Label(dialog,text='先匹配完整进程路径，再匹配窗口条件；多个主窗口且条件不唯一时将停止切换。',wraplength=720).grid(row=6,column=0,columnspan=2,padx=10,pady=8)
        def apply():
            try:
                target={key:var.get().strip() for key,var in values.items()};target['command']=json.loads(target['command'])
                target.update(id=preset.get('id') or 'desktop-'+uuid.uuid4().hex[:10],adapter='desktop',pin=pin.get())
                self.store_target(target);dialog.destroy()
            except (ValueError,TypeError,OSError) as exc:messagebox.showerror('目标配置无效',str(exc),parent=dialog)
        ttk.Button(dialog,text='保存并选择',command=apply).grid(row=7,column=1,sticky='e',padx=10)

    def store_target(self,target):
        config=copy.deepcopy(self.config)
        config['targets']=[t for t in config['targets'] if t['id']!=target['id']]+[target]
        config['study_target']=target['id'];validate_config(config)
        self.config=config
        self.target_box.configure(values=[t['name'] for t in config['targets']]);self.target_choice.set(target['name'])
        self.target_pin.set(target.get('pin',True))
        if not self.save():raise ValueError('未能保存目标配置')

    def open_catalog_entry(self,entry):
        target=self.catalog.web_target(entry)
        self.mode.set('study');self.refresh_mode_visibility();self.store_target(target)
        manager=self.registry.get(target)
        threading.Thread(target=lambda:manager.ensure(manual=True),daemon=True).start()

    def add_web(self):
        dialog=tk.Toplevel(self.root);dialog.title('按名称搜索官方学习入口');dialog.geometry('860x510');dialog.transient(self.root)
        query=tk.StringVar()
        line=ttk.Frame(dialog);line.pack(fill='x',padx=12,pady=10)
        ttk.Label(line,text='软件官方名称 / 别名：').pack(side='left')
        entry=ttk.Entry(line,textvariable=query,width=38);entry.pack(side='left',padx=8);entry.focus_set()
        message=tk.StringVar(value=f'已收录 {len(self.catalog.entries)} 个入口；输入名称后回车即可打开。')
        ttk.Label(dialog,textvariable=message,wraplength=820).pack(anchor='w',padx=12,pady=5)
        listing=ttk.Treeview(dialog,columns=('name','support','type'),show='headings',height=10)
        for key,title,width in [('name','学习软件',240),('support','电脑端支持',180),('type','类别',180)]:
            listing.heading(key,text=title);listing.column(key,width=width)
        listing.pack(fill='both',expand=True,padx=12)
        results=[]
        detail=tk.StringVar();ttk.Label(dialog,textvariable=detail,wraplength=820).pack(anchor='w',padx=12,pady=8)
        def refresh(*args):
            results[:]=self.catalog.search(query.get());listing.delete(*listing.get_children())
            for index,item in enumerate(results):
                listing.insert('', 'end',iid=str(index),values=(item['name'],'已核实 Windows 客户端' if item.get('windows') is True else '网页 / 未核实电脑客户端',item['category']))
        def selected():
            choice=listing.selection()
            return results[int(choice[0])] if choice else None
        def details(*args):
            item=selected()
            if item:detail.set(item['note']+'\n'+(item.get('study_url') or item['official_url']))
        def open_result(*args):
            item=self.catalog.resolve(query.get()) or selected()
            if item:
                try:self.open_catalog_entry(item);dialog.destroy()
                except (ValueError,OSError) as exc:messagebox.showerror('未能设置官网',str(exc),parent=dialog)
            elif results:
                message.set('有多个或相近的名称，请双击要使用的项目；不会自动选搜索首条广告。')
            else:
                self.search_unknown_official(query.get(),dialog)
        query.trace_add('write',refresh);listing.bind('<<TreeviewSelect>>',details)
        listing.bind('<Double-1>',open_result);entry.bind('<Return>',open_result)
        ttk.Button(line,text='搜索并打开官网',command=open_result).pack(side='left')
        ttk.Button(dialog,text='补充未收录官网（仅首次需确认网址）',command=lambda:self.add_web_address(query.get())).pack(anchor='e',padx=12,pady=8)
        refresh()

    def search_unknown_official(self,name,parent=None):
        try:self.catalog.search_target(name)
        except ValueError as exc:messagebox.showerror('名称无效',str(exc),parent=parent or self.root);return
        dialog=tk.Toplevel(parent or self.root);dialog.title('待核实官网候选：'+name);dialog.geometry('850x410');dialog.transient(self.root)
        state=tk.StringVar(value='正在按软件名称联网搜索；候选结果未认证为官网，请核对名称与域名。')
        ttk.Label(dialog,textvariable=state,wraplength=810).pack(anchor='w',padx=12,pady=10)
        listing=ttk.Treeview(dialog,columns=('title','domain'),show='headings',height=9)
        listing.heading('title',text='待核实搜索结果');listing.column('title',width=550)
        listing.heading('domain',text='域名');listing.column('domain',width=250);listing.pack(fill='both',expand=True,padx=12)
        pending=queue.Queue();items=[]
        def search():
            try:pending.put(online_candidates(name))
            except Exception:pending.put([])
        threading.Thread(target=search,daemon=True).start()
        def browser_search():
            manager=self.registry.get(self.catalog.search_target(name))
            threading.Thread(target=lambda:manager.ensure(manual=True),daemon=True).start()
        def collect():
            if not dialog.winfo_exists():return
            try:items[:]=pending.get_nowait()
            except queue.Empty:dialog.after(100,collect);return
            for index,item in enumerate(items):listing.insert('', 'end',iid=str(index),values=(item['title'],item['domain']))
            if not items:state.set('搜索未返回可核对的候选或网络不可用，已打开搜索页；可稍后重试或补充官网。');browser_search()
            else:state.set('选中并确认一个官网即可保存并打开，无需复制网址；这些候选尚未核实为官方。')
        def confirm():
            selection=listing.selection()
            if not selection:return
            try:
                entry=self.catalog.remember(name,items[int(selection[0])]['url']);self.open_catalog_entry(entry);dialog.destroy()
            except (ValueError,OSError) as exc:messagebox.showerror('未能保存官网',str(exc),parent=dialog)
        buttons=ttk.Frame(dialog);buttons.pack(anchor='e',padx=12,pady=8)
        ttk.Button(buttons,text='在浏览器查看搜索',command=browser_search).pack(side='left',padx=5)
        ttk.Button(buttons,text='确认选中官网并打开',command=confirm).pack(side='left',padx=5)
        dialog.after(100,collect)

    def add_web_address(self,preset_name=''):
        dialog=tk.Toplevel(self.root);dialog.title('补充用户确认的官网');dialog.geometry('740x240');dialog.transient(self.root)
        name=tk.StringVar(value=preset_name);url=tk.StringVar()
        ttk.Label(dialog,text='官方名称').pack(anchor='w',padx=12,pady=5);ttk.Entry(dialog,textvariable=name,width=70).pack(padx=12)
        ttk.Label(dialog,text='已确认的 HTTPS 官网地址（保存后下次按名称搜索）').pack(anchor='w',padx=12,pady=5);ttk.Entry(dialog,textvariable=url,width=70).pack(padx=12)
        def apply():
            try:
                item=self.catalog.remember(name.get().strip(),url.get().strip());self.open_catalog_entry(item);dialog.destroy()
            except (ValueError,OSError) as exc:messagebox.showerror('官网配置无效',str(exc),parent=dialog)
        ttk.Button(dialog,text='保存并打开',command=apply).pack(anchor='e',padx=12,pady=10)

    def discover_apps(self):
        dialog=tk.Toplevel(self.root);dialog.title('检测电脑上的学习软件');dialog.geometry('1000x570');dialog.transient(self.root)
        label=ttk.Label(dialog,text='正在按软件库匹配安装登记、开始菜单、运行进程和 Anki 常见目录…');label.pack(anchor='w',padx=12,pady=8)
        line=ttk.Frame(dialog);line.pack(fill='x',padx=12)
        ttk.Label(line,text='按名称过滤：').pack(side='left');query=tk.StringVar();ttk.Entry(line,textvariable=query,width=30).pack(side='left',padx=8)
        show_missing=tk.BooleanVar(value=False)
        table=ttk.Treeview(dialog,columns=('name','state','path'),show='headings',height=14)
        for key,title,width in [('name','学习软件',200),('state','检测依据',170),('path','实际程序位置',570)]:table.heading(key,text=title);table.column(key,width=width)
        table.pack(fill='both',expand=True,padx=12,pady=8)
        detected=[];visible=[];pending=queue.Queue()
        def refresh(*args):
            ids={e['id'] for e in self.catalog.search(query.get(),windows_only=True)}
            items=[i for i in detected if i['catalog_id'] in ids or
                   (i['catalog_id'].startswith('configured:') and query.get().casefold() in i['name'].casefold())]
            if show_missing.get():
                have={i['catalog_id'] for i in detected}
                items += [dict(name=e['name'],catalog_id=e['id'],source='未检测到',path='') for e in self.catalog.search(query.get(),windows_only=True) if e['id'] not in have]
            visible[:]=items;table.delete(*table.get_children())
            for index,item in enumerate(items):table.insert('', 'end',iid=str(index),values=(item['name'],item['source'],item['path']))
        query.trace_add('write',refresh)
        ttk.Checkbutton(line,text='同时查看库中未检测到的软件',variable=show_missing,command=refresh).pack(side='left')
        def scan():
            try:pending.put((scan_learning_apps(self.catalog,self.windows,ROOT,configured_targets=self.config['targets']),''))
            except Exception as exc:pending.put(([],type(exc).__name__))
        threading.Thread(target=scan,daemon=True).start()
        def collect():
            if not dialog.winfo_exists():return
            try:items,error=pending.get_nowait()
            except queue.Empty:dialog.after(100,collect);return
            detected[:]=items;refresh()
            count=sum(e.get('windows') is True for e in self.catalog.entries)
            label.configure(text=f'已识别 {len(items)} 个本机学习程序；目录包含 {count} 类 Windows 学习软件。'+('扫描未完成：'+error if error else ' 未安装不等于不支持；仅显示实际找到的路径。'))
        def choice():
            selection=table.selection();return visible[int(selection[0])] if selection else None
        def choose():
            item=choice()
            if not item:return
            if not item['path']:messagebox.showinfo('未检测到本机安装','可查看官网或通过文件选择器添加；不会自动安装。',parent=dialog);return
            try:
                target=detected_target(item,self.config['targets']);self.mode.set('study');self.refresh_mode_visibility();self.store_target(target);dialog.destroy()
            except (ValueError,OSError) as exc:messagebox.showerror('未能设置目标',str(exc),parent=dialog)
        def open_site():
            item=choice()
            if item:
                known=next((e for e in self.catalog.entries if e['id']==item['catalog_id']),None)
                if not known:messagebox.showinfo('未收录官网','这是你已有的自选目标，可通过名称搜索补充官方入口。',parent=dialog);return
                self.open_catalog_entry(known);dialog.destroy()
        buttons=ttk.Frame(dialog);buttons.pack(anchor='e',padx=12,pady=8)
        ttk.Button(buttons,text='查看所选官网',command=open_site).pack(side='left',padx=5)
        ttk.Button(buttons,text='选择使用（首次需要时启动）',command=choose).pack(side='left',padx=5)
        ttk.Button(buttons,text='手动选择其他程序',command=self.add_desktop).pack(side='left',padx=5)
        table.bind('<Double-1>',lambda _:choose());dialog.after(100,collect)

    def log_return_result(self,presentation,result):
        if result in {'returned','failed','cancelled'}:
            self.events.put(('log',('返回游戏成功：' if result=='returned' else '返回游戏未完成：')+presentation.last_return_reason))
            if result=='failed':self.events.put(('bell',''))

    def start(self):
        if self.worker and self.worker.is_alive():return
        if not self.save(report=False):return
        self.stop_event=threading.Event()
        self.rediscover()
        self.worker=threading.Thread(target=self.monitor,args=(copy.deepcopy(self.config),self.stop_event),daemon=True);self.worker.start()
        self.append('自动监测已启动；目标首次需要时启动，手动关闭后不反复拉起。')

    def monitor(self,config,stop_event):
        from lol_words.application.monitoring import run_monitor
        run_monitor(self,config,stop_event,ROOT,GameData,SceneCapture)

    def stop(self):
        self.stop_event.set();self.status.set('正在暂停并清理…')

    def tick(self):
        while True:
            try:kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='log':self.append(value)
            elif kind=='status':self.status.set(value)
            elif kind=='penalty':self.penalty_status.set(value)
            elif kind=='download_request':self.show_download_prompt(value)
            elif kind=='discovery':
                target_id,path,updates=value;self.path_label.set(('发现目标启动程序：'+path) if path else '未自动发现；可使用已安装软件列表或文件选择器添加')
                if path:
                    config=copy.deepcopy(self.config)
                    for target in config['targets']:
                        if target['id']==target_id:target.update(updates)
                    self.config=config;self.save(report=False)
            elif kind=='bell':self.root.bell()
        pressed=self.windows.stop_pressed()
        if pressed and not self.hotkey_down:self.stop()
        self.hotkey_down=pressed
        if self.closing and not(self.worker and self.worker.is_alive()):self.root.destroy();return
        self.root.after(100,self.tick)

    def close(self):
        self.closing=True;self.stop_event.set();self.status.set('正在退出并恢复窗口状态…')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    C.WinDLL('user32').SetProcessDPIAware()
    root=tk.Tk();app=App(root,autostart=not args.check)
    if args.check:
        root.update();capture=SceneCapture()
        try:
            pixels=capture.read(root.winfo_id())
            if not pixels:raise RuntimeError('界面采样检查失败')
            print(f'界面与采样通过：{len(pixels)} 字节；后端={capture.backend}；未启动监测、学习软件、娱乐页面或下载。')
        finally:capture.close();root.destroy()
    else:root.mainloop()


if __name__=='__main__':main()
