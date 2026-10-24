"""构建 Windows 便携程序；只收集运行内容，不带个人资料、测试或归档。"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import tempfile
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.verify_exe import verify_folder, check_inventory


def source_identity(root):
    """Source ZIPs can be rebuilt without Git; never attribute a parent repository."""
    try:
        top=subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
        if Path(top).resolve()!=Path(root).resolve():return None,None
        revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip())
        return revision,dirty
    except (OSError,subprocess.CalledProcessError):
        return None,None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--existing-dist',type=Path,help='收集本工具已生成的 LOL-Words 程序目录')
    args=parser.parse_args()
    output=args.output.resolve()
    if output.suffix.lower()!='.zip':raise ValueError('输出文件需要 .zip 后缀')
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists() or output.with_suffix('.zip.sha256').exists():
        raise ValueError('输出文件已存在，拒绝覆盖：'+str(output))
    if args.existing_dist:
        dist=args.existing_dist.resolve()
    else:
        work=Path(tempfile.mkdtemp(prefix='exe-build-',dir=os.environ.get('PI_SCRATCH_DIR')))
        env=dict(os.environ);env.pop('LOL_WORDS_BUILD_CONSOLE',None)
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(work/'dist'),
            '--workpath',str(work/'work'),str(ROOT/'build_config/windows.spec')],cwd=ROOT,env=env,check=True)
        dist=work/'dist/LOL-Words'
    runtime_name=f'python{sys.version_info.major}{sys.version_info.minor}.dll'
    if not (dist/'LOL-Words.exe').is_file() or not (dist/'_internal'/runtime_name).is_file():
        raise ValueError('缺少程序或内置 Python 运行库，构建未完成')
    check_inventory(dist)
    folder=output.parent/(output.stem+'-files')/'LOL-Words'
    if folder.exists():raise ValueError('目标目录已存在，为保留配置已停止覆盖：'+str(folder))
    shutil.copytree(dist,folder)
    for name in ['config.example.json','learning_catalog.json','LICENSE']:shutil.copy2(ROOT/name,folder/name)
    shutil.copytree(ROOT/'assets',folder/'assets')
    shutil.copytree(ROOT/'licenses',folder/'licenses')
    (folder/'docs').mkdir();shutil.copy2(ROOT/'docs/THIRD_PARTY_NOTICES.md',folder/'docs/THIRD_PARTY_NOTICES.md')
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():
        target=folder/'licenses/Python';target.mkdir();shutil.copy2(python_license,target/'LICENSE.txt')
    distribution=importlib.metadata.distribution('pyinstaller')
    for item in distribution.files or []:
        if '.dist-info/licenses/' in item.as_posix():
            target=folder/'licenses/PyInstaller'/item.as_posix().split('.dist-info/licenses/',1)[1]
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(distribution.locate_file(item),target)
    (folder/'使用说明.txt').write_text('''泉水时间管理 · Windows 便携版

1. 完整解压本压缩包，双击 LOL-Words.exe。无需安装 Python。
2. 保留整个程序目录，运行库、assets 和产品目录文件都随程序使用。
3. 首次默认学习模式、Anki、死亡后延迟 3 秒、复活前提前返回 1 秒。
4. 学习软件和 Edge / Chrome 由用户自行安装，可在界面中检测、添加或选择官网。
5. Ctrl + Alt + F8 暂停监测。遇到窗口切换权限不足时，退出工具后右键程序选择“以管理员身份运行”。
6. 配置 config.json、runtime 运行资料及日志保存于程序同目录。升级时先退出工具，再迁移自己的配置和 runtime。

此版本不包含大厅自动接受、转让队长、返回大厅或图像恢复。
已通过本机隔离启动自检（中文及空格路径、五页界面、资源与画面采样），不代表所有真实游戏、英雄和第三方软件组合均已实测。
''',encoding='utf-8')
    validation=verify_folder(folder)  # 必须成功才能生成交付 ZIP；不会在交付目录产生日志。
    (folder/'VALIDATION.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
    revision,dirty=source_identity(ROOT)
    manifest=dict(format_version=1,kind='windows_x64_portable',requires_python_installation=False,
        python=sys.version.split()[0],pyinstaller=importlib.metadata.version('pyinstaller'),startup_verified=True,
        source_base=revision,source_working_tree_modified=dirty,files={})
    files=sorted(p for p in folder.rglob('*') if p.is_file())
    for path in files:
        raw=path.read_bytes();manifest['files'][path.relative_to(folder).as_posix()]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    (folder/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary=output.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for path in sorted(p for p in folder.rglob('*') if p.is_file()):bundle.write(path,'LOL-Words/'+path.relative_to(folder).as_posix())
    with zipfile.ZipFile(temporary) as bundle:
        assert bundle.testzip() is None
        for name,item in manifest['files'].items():assert hashlib.sha256(bundle.read('LOL-Words/'+name)).hexdigest()==item['sha256'],name
    temporary.replace(output)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps(dict(zip=str(output),folder=str(folder),exe=str(folder/'LOL-Words.exe'),bytes=output.stat().st_size,sha256=digest),ensure_ascii=False))


if __name__=='__main__':main()
