"""自动查找 Anki：缓存、运行进程、注册表、快捷方式、各磁盘常用软件目录。"""
from pathlib import Path
import ctypes
import json
import os
import re
import shutil
import subprocess


EXECUTABLES = ("Anki.exe", "anki.exe", "anki-launcher.exe", "ankiw.exe")


def executable_from(value):
    if not value:
        return None
    text = os.path.expandvars(str(value).strip())
    quoted = re.match(r'^"([^"]+\.exe)"', text, re.I)
    if quoted:
        text = quoted.group(1)
    else:
        match = re.match(r"(.+?\.exe)(?:,\s*-?\d+|\s.*)?$", text, re.I)
        if match:
            text = match.group(1)
    path = Path(text)
    if path.is_file() and path.name.casefold() in {name.casefold() for name in EXECUTABLES}:
        return path.resolve()
    if path.is_dir():
        for child in (path, path / "Launcher", path / "Anki", path / "Anki" / "Launcher"):
            for name in EXECUTABLES:
                candidate = child / name
                if candidate.is_file():
                    return candidate.resolve()
    return None


def registry_candidates():
    import winreg
    results = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for app in ("anki.exe", "Anki.exe", "anki-launcher.exe"):
                try:
                    with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{app}",
                                        0, winreg.KEY_READ | view) as key:
                        results.append(winreg.QueryValueEx(key, "")[0])
                except OSError:
                    pass
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                                    0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as sub:
                                name = winreg.QueryValueEx(sub, "DisplayName")[0]
                                if not re.search(r"\banki\b", str(name), re.I):
                                    continue
                                for field in ("InstallLocation", "DisplayIcon"):
                                    try:
                                        results.append(winreg.QueryValueEx(sub, field)[0])
                                    except OSError:
                                        pass
                        except OSError:
                            continue
            except OSError:
                pass
    return results


def shortcut_candidates():
    # 仅解析名字包含 Anki 的快捷方式，不执行快捷方式目标。
    script = r"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$roots = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs", [Environment]::GetFolderPath('Desktop'))
$shell = New-Object -ComObject WScript.Shell
$targets = @($roots | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object {
  Get-ChildItem -LiteralPath $_ -Filter '*anki*.lnk' -Recurse -ErrorAction SilentlyContinue |
  ForEach-Object { $shell.CreateShortcut($_.FullName).TargetPath }
})
ConvertTo-Json -InputObject $targets -Compress
"""
    try:
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        # 明确要求子进程按 UTF-8 输出，避免非英文安装路径被系统代码页破坏。
        raw = result.stdout.decode("utf-8-sig", errors="replace").strip()
        value = json.loads(raw) if raw else []
        return value if isinstance(value, list) else [value]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []


def local_roots():
    roots = []
    kernel = ctypes.windll.kernel32
    kernel.GetLogicalDrives.restype = ctypes.c_uint
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    mask = kernel.GetLogicalDrives()
    for index in range(26):
        if mask & (1 << index):
            drive = Path(f"{chr(65 + index)}:/")
            # 不扫描网络盘或光驱。
            if kernel.GetDriveTypeW(str(drive)) in (2, 3):
                roots.append(drive)
    return roots


def locate_anki(cached="", project_root=None, running_paths=()):
    candidates = [cached, *running_paths, shutil.which("anki")]
    candidates += registry_candidates()
    roots = local_roots()
    for drive in roots:
        for folder in ("Anki", "codex/Anki", "Codex/Anki", "Program Files/Anki",
                       "Program Files (x86)/Anki", "Programs/Anki", "Software/Anki",
                       "软件/Anki", "应用/Anki", "Apps/Anki", "Tools/Anki"):
            candidates.append(drive / folder)
    candidates += [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Anki",
                   Path(os.environ.get("LOCALAPPDATA", "")) / "Anki"]
    if project_root:
        for parent in [Path(project_root), *list(Path(project_root).parents)[:3]]:
            candidates += [parent / "Anki", parent / "anki"]
    for candidate in candidates:
        if found := executable_from(candidate):
            return str(found)
    for candidate in shortcut_candidates():
        if found := executable_from(candidate):
            return str(found)
    # 最后做有限深度搜索，只查常见软件目录，不遍历用户文档和系统目录。
    for drive in roots:
        for folder in ("codex", "Programs", "Software", "软件", "Apps", "Tools"):
            base = drive / folder
            if not base.is_dir():
                continue
            visited = 0
            for current, directories, _ in os.walk(base, followlinks=False):
                visited += 1
                depth = len(Path(current).relative_to(base).parts)
                directories[:] = [name for name in directories
                                   if not name.startswith('.') and name.casefold() not in
                                   {'node_modules', '__pycache__', 'app_packages', 'site-packages', '.venv'}]
                if depth >= 3 or visited >= 1200:
                    directories[:] = []
                if "anki" in Path(current).name.casefold():
                    if found := executable_from(current):
                        return str(found)
                if visited >= 1200:
                    break
    return ""
