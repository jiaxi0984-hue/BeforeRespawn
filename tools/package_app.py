"""生成源码包及含依赖运行包；显式排除用户配置、资料与无关本地文件。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CODE = ['launchers/start.pyw', 'start-admin.cmd',
        'requirements.txt', 'requirements-capture.txt', 'start.cmd', 'install.cmd', 'config.example.json', 'learning_catalog.json',
        'README.md', 'LICENSE', '.gitignore', '.gitattributes']


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-deps', action='store_true'); args = parser.parse_args()
    files = [ROOT / name for name in CODE]
    files += list((ROOT/'lol_words').rglob('*.py'))
    files += list((ROOT/'tests').glob('test_*.py')) + list((ROOT/'tools').glob('*.py'))
    for directory in ['docs', 'licenses', 'assets']:
        if (ROOT/directory).exists(): files += [p for p in (ROOT/directory).rglob('*') if p.is_file()]
    if args.with_deps:
        for name in ['PySide6', 'shiboken6', 'dxcam', 'comtypes', 'numpy', 'websocket']:
            if not (ROOT/'_deps'/name).is_dir(): raise RuntimeError('缺少运行依赖：'+name)
        files += [p for p in (ROOT/'_deps').rglob('*') if p.is_file()]
    files = sorted(set(p for p in files if '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'}))
    revision = None
    try:
        result = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, text=True, capture_output=True)
        if result.returncode == 0 and Path(result.stdout.strip()).resolve() == ROOT:
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), bundled_dependencies=args.with_deps,
        python='3.14 x64', game_verification='pending', downloads_performed=False,
        source_snapshot='working_tree',
        source_base=revision, files={})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            assert relative not in {'config.json', 'runtime-python.txt', 'runtime.log'}
            assert not relative.startswith(('runtime/', '.git/', 'backups/'))
            raw = path.read_bytes()
            manifest['files'][relative] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            archive.writestr(relative, raw)
        archive.writestr('BUILD-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(temporary) as archive:
        assert archive.testzip() is None
        for name, entry in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == entry['sha256'], name
    temporary.replace(args.output)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix('.zip.sha256').write_text(digest+'  '+args.output.name+'\n', encoding='utf-8')
    print(json.dumps(dict(path=str(args.output), bytes=args.output.stat().st_size, files=len(files), sha256=digest), ensure_ascii=False))


if __name__ == '__main__': main()
