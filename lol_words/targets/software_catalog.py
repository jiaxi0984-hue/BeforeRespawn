"""已安装桌面软件的发现入口；不猜测品牌客户端或访问浏览器个人资料。"""
import json
import os
from pathlib import Path
import re
import subprocess


def split_arguments(arguments):
    """按 Windows 规则解析快捷方式参数，绝不执行命令行外壳。"""
    if not arguments:return []
    import ctypes as C
    from ctypes import wintypes as W
    shell=C.WinDLL('shell32');kernel=C.WinDLL('kernel32')
    shell.CommandLineToArgvW.argtypes=[W.LPCWSTR,C.POINTER(C.c_int)]
    shell.CommandLineToArgvW.restype=C.POINTER(W.LPWSTR)
    kernel.LocalFree.argtypes=[W.HLOCAL]
    count=C.c_int();values=shell.CommandLineToArgvW('program.exe '+str(arguments),C.byref(count))
    if not values:raise ValueError('快捷方式参数无效')
    try:return [values[i] for i in range(1,count.value)]
    finally:kernel.LocalFree(values)


def installed_desktop_apps():
    # 只读取卸载登记和开始菜单快捷方式，用户文档不参与扫描。
    script = r'''
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$items = @()
$roots = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
foreach($item in (Get-ItemProperty $roots -ErrorAction SilentlyContinue)) {
 if($item.DisplayName) { $items += @{name=$item.DisplayName; path=$item.DisplayIcon; install_location=$item.InstallLocation; publisher=$item.Publisher; source='安装登记'} }
}
$shell = New-Object -ComObject WScript.Shell
foreach($root in @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs", [Environment]::GetFolderPath('Desktop'))) {
 Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1000 | ForEach-Object {
  $link=$shell.CreateShortcut($_.FullName)
  if($link.TargetPath) { $items += @{name=$_.BaseName; path=$link.TargetPath; arguments=$link.Arguments; source='开始菜单'} }
 }
}
$appPaths=@('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\*','HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\*')
foreach($app in (Get-ItemProperty $appPaths -ErrorAction SilentlyContinue)) {
 $path=$app.'(default)'
 if($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
  $name=(Get-Item -LiteralPath $path).VersionInfo.ProductName
  if(-not $name){$name=[IO.Path]::GetFileNameWithoutExtension($path)}
  $items+=@{name=$name;path=$path;source='应用路径登记'}
 }
}
ConvertTo-Json -InputObject @($items) -Compress
'''
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise OSError('安装登记采集未完成')
    items = json.loads(result.stdout.decode('utf-8-sig') or '[]')
    found = {}
    for item in items:
        match = re.match(r'^\s*"?(.+?\.exe)(?:"|,|$)', str(item.get('path', '')), re.I)
        if match:
            path = Path(os.path.expandvars(match[1]))
            if path.is_file():
                found[(str(path.resolve()).casefold(),item['name'])] = dict(item, path=str(path.resolve()))
    # 安装位置存在但缺少 DisplayIcon 的记录也保留，后续仅在实际安装目录内查找。
    for item in items:
        if item.get('install_location') and not any(key[1]==item['name'] for key in found):
            found[('install',item['name'])]=item
    return sorted(found.values(), key=lambda item: item['name'].casefold())


def scan_learning_apps(catalog, windows=None, project_root=None, records=None, configured_targets=(), strict=False):
    """库中产品名称与本机事实交叉匹配；库本身不携带猜测的安装路径。"""
    provided=records is not None
    if not provided:
        try:records=installed_desktop_apps()
        except (OSError,ValueError,subprocess.TimeoutExpired):
            if strict:raise
            records=[]
    records=list(records)
    if windows is not None:
        for row in windows.window_inventory():
            records.append(dict(name=Path(row['path']).stem,path=row['path'],source='运行进程'))
    found={}
    helpers={'uninstall.exe','unins000.exe','unins001.exe','setup.exe','update.exe',
             'rundll32.exe','explorer.exe','msedge.exe','chrome.exe','cmd.exe','powershell.exe',
             'pwsh.exe','wscript.exe','cscript.exe','mshta.exe','python.exe','pythonw.exe'}
    for record in records:
        entry=catalog.identify_installation(record.get('name',''),record.get('product_name',''))
        if not entry:continue
        original=Path(record.get('path') or '')
        candidates=[]
        if original.is_file() and original.suffix.lower()=='.exe' and original.name.lower() not in helpers:
            candidates.append(original)
        location=record.get('install_location')
        if not candidates and location:
            base=Path(location)
            # 只读已登记产品自己的安装目录；不递归扫描系统根目录或文档。
            if base.is_dir() and base.is_absolute() and base!=Path(base.anchor):
                visited=0
                for current,dirs,files in os.walk(base,followlinks=False):
                    visited+=1
                    if len(Path(current).relative_to(base).parts)>=2:dirs[:]=[]
                    if visited>=250:dirs[:]=[]
                    for name in files:
                        path=Path(current)/name
                        if path.suffix.lower()!='.exe' or path.name.lower() in helpers:continue
                        match=catalog.identify_installation(path.stem)
                        if match and match['id']==entry['id']:candidates.append(path)
                    if visited>=250:break
        if len({str(p.resolve()).casefold() for p in candidates})!=1:continue
        process=candidates[0].resolve()
        try:
            args=split_arguments(record.get('arguments',''))
        except (OSError,ValueError):
            continue
        launch=original.resolve() if original.is_file() else process
        if launch.name.lower() in helpers:launch=process;args=[]
        item=dict(name=entry['name'],path=str(process),command=[str(launch),*args],
            process_path=str(process),catalog_id=entry['id'],source=record.get('source','安装登记'),
            official_url=entry['official_url'],category=entry['category'])
        found[(entry['id'],str(process).casefold())]=item
    if not provided and not any(item['catalog_id']=='anki' for item in found.values()):
        from lol_words.targets.discovery import locate_anki
        path=locate_anki(project_root=project_root,running_paths=windows.anki_paths() if windows else ())
        if path:
            found[('anki',path.casefold())]=dict(name='Anki',path=path,command=[path],process_path=path,
                catalog_id='anki',source='Anki 自动发现',official_url='https://apps.ankiweb.net/',category='背词与词典')
    for target in configured_targets:
        if target.get('adapter')!='desktop':continue
        path=target.get('process_path','')
        if not path or not Path(path).is_file():continue
        if any(item['path'].casefold()==str(Path(path).resolve()).casefold() for item in found.values()):continue
        found[(target['id'],path.casefold())]=dict(name=target['name'],path=str(Path(path).resolve()),
            process_path=str(Path(path).resolve()),command=target['command'],catalog_id='configured:'+target['id'],
            source='已有学习目标配置',category='用户自选',official_url='',configured_target=dict(target))
    return sorted(found.values(),key=lambda item:(item['name'].casefold(),item['path'].casefold()))


def detected_target(item, existing=()):
    import hashlib
    if item.get('configured_target'):return dict(item['configured_target'])
    path=str(Path(item['process_path']).resolve())
    identity=('anki' if item['catalog_id']=='anki' else 'catalog-desktop-'+item['catalog_id']+'-'+
              hashlib.sha256(path.casefold().encode()).hexdigest()[:8])
    previous=next((t for t in existing if t['id']==identity),{})
    name=previous.get('name',item['name'])
    occupied={t['name'] for t in existing if t['id']!=identity}
    number=2
    while name in occupied:name=f"{item['name']}（{number}）";number+=1
    return dict(previous,id=identity,name=name,adapter='anki' if item['catalog_id']=='anki' else 'desktop',
        command=item['command'],process_path=path,title_contains=previous.get('title_contains',''),
        window_class=previous.get('window_class',''),pin=previous.get('pin',True),catalog_id=item['catalog_id'])
