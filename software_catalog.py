"""已安装桌面软件的发现入口；不猜测品牌客户端或访问浏览器个人资料。"""
import json
import os
from pathlib import Path
import re
import subprocess


def installed_desktop_apps():
    # 只读取卸载登记和开始菜单快捷方式，用户文档不参与扫描。
    script = r'''
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$items = @()
$roots = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
foreach($item in (Get-ItemProperty $roots -ErrorAction SilentlyContinue)) {
 if($item.DisplayName -and $item.DisplayIcon) { $items += @{name=$item.DisplayName; path=$item.DisplayIcon} }
}
$shell = New-Object -ComObject WScript.Shell
foreach($root in @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs")) {
 Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1000 | ForEach-Object {
  $link=$shell.CreateShortcut($_.FullName)
  if($link.TargetPath -and -not $link.Arguments) { $items += @{name=$_.BaseName; path=$link.TargetPath} }
 }
}
ConvertTo-Json -InputObject @($items) -Compress
'''
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    items = json.loads(result.stdout.decode('utf-8-sig') or '[]')
    found = {}
    for item in items:
        match = re.match(r'^\s*"?(.+?\.exe)(?:"|,|$)', str(item.get('path', '')), re.I)
        if match:
            path = Path(os.path.expandvars(match[1]))
            if path.is_file():
                found[str(path.resolve()).casefold()] = dict(name=item['name'], path=str(path.resolve()))
    return sorted(found.values(), key=lambda item: item['name'].casefold())
