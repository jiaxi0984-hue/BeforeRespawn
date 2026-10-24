"""隔离验收便携 EXE；不监测游戏、不打开目标，不改写待验目录。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

PRIVATE_NAMES = {'config.json', 'runtime.log', 'runtime-python.txt', 'startup-error.log'}
PRIVATE_PARTS = {'runtime', 'archive', 'tests', '.git', '_deps'}


def check_inventory(folder):
    folder = Path(folder)
    for path in folder.rglob('*'):
        if path.is_symlink():
            raise ValueError('不接受符号链接：' + str(path))
        relative = path.relative_to(folder)
        if path.name.casefold() in PRIVATE_NAMES or PRIVATE_PARTS.intersection(part.casefold() for part in relative.parts):
            raise ValueError('混入个人资料或非运行内容：' + str(relative))
    if not (folder/'LOL-Words.exe').is_file():
        raise ValueError('缺少 LOL-Words.exe')


def clean_environment():
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'QT_PLUGIN_PATH',
                'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_PLATFORM', 'QML2_IMPORT_PATH'):
        env.pop(key, None)
    windows = Path(env.get('SystemRoot', r'C:\Windows'))
    env['PATH'] = os.pathsep.join(map(str, [windows/'System32', windows]))
    env['PYTHONUTF8'] = '1'
    return env


def verify_folder(folder):
    """Run a copied folder so generated logs cannot contaminate the deliverable."""
    folder = Path(folder).resolve()
    check_inventory(folder)
    with tempfile.TemporaryDirectory(prefix='验包 EXE 中文 空格 ', dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
        root = Path(temporary)
        app = root/'独立 程序'/'LOL-Words'
        shutil.copytree(folder, app)
        cwd = root/'无源码 工作目录'
        cwd.mkdir()
        report = root/'startup.json'
        run = subprocess.run([str(app/'LOL-Words.exe'), '--check', '--check-report', str(report)],
                             cwd=cwd, env=clean_environment(), capture_output=True, timeout=60)
        result = json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
        valid = (run.returncode == 0 and result.get('ok') and result.get('frozen')
                 and result.get('pages') == 5 and result.get('resources_valid')
                 and result.get('bytes', 0) > 0
                 and Path(result.get('root', '')).resolve() == app.resolve())
        if not valid:
            raise RuntimeError('EXE 隔离启动失败：' + json.dumps(dict(exit_code=run.returncode,
                check=result, stderr=run.stderr.decode('utf-8', errors='replace')[-4000:]), ensure_ascii=False))
        return dict(ok=True, exit_code=run.returncode, frozen=True, pages=result['pages'],
                    resources_valid=True, capture_bytes=result['bytes'], backend=result['backend'],
                    chinese_space_path=True, external_python_path_removed=True,
                    unrelated_working_directory=True, root_matches_executable=True,
                    real_game_verified=False, downloads_performed=False)


def verify_zip(path):
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix='解压 验包 ', dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
        target = Path(temporary)
        with zipfile.ZipFile(path) as bundle:
            if bundle.testzip() is not None:
                raise ValueError('ZIP 完整性检查失败')
            names = bundle.namelist()
            if len(names) != len(set(names)):
                raise ValueError('ZIP 含重复成员')
            for item in bundle.infolist():
                if ('\\' in item.filename or not item.filename.startswith('LOL-Words/')
                    or not (target/item.filename).resolve().is_relative_to(target)
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError('ZIP 成员路径不安全：' + item.filename)
            manifest = json.loads(bundle.read('LOL-Words/BUILD-MANIFEST.json'))
            expected = {'LOL-Words/'+name for name in manifest['files']} | {'LOL-Words/BUILD-MANIFEST.json'}
            if set(names) != expected:
                raise ValueError('ZIP 成员与构建清单不一致')
            for name, entry in manifest['files'].items():
                raw = bundle.read('LOL-Words/'+name)
                if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                    raise ValueError('ZIP 摘要不一致：' + name)
            bundle.extractall(target)
        result = verify_folder(target/'LOL-Words')
        result.update(zip_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), manifest_verified=True)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('zip', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('报告已存在，拒绝覆盖：' + str(args.report))
    result = verify_zip(args.zip)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
