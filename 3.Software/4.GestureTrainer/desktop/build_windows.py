"""Build the Windows portable product using an existing verified training venv.

Run on Windows. Build artifacts and user data must be in different directories.
The packaged interpreter never refers back to the installed Python/venv.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from slim_windows import prune_development_files

SOURCE=Path(__file__).resolve().parents[1]
EXCLUDE={'__pycache__','.git','.pio','.vscode','idlelib','tkinter'}

def copy_tree(source,dest):
    shutil.copytree(source,dest,dirs_exist_ok=True,ignore=shutil.ignore_patterns(*EXCLUDE,'*.pyc','*.pyo'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training-python',type=Path,required=True)
    parser.add_argument('--window-python',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seed',type=Path,required=True)
    parser.add_argument('--license',type=Path,required=True)
    parser.add_argument('--zip',action='store_true')
    args=parser.parse_args()
    if sys.platform!='win32':raise SystemExit('Windows build required.')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if (output/'build-manifest.json').exists():raise SystemExit('Please use a fresh output folder for a reproducible build.')
    runtime=output/'runtime';app=output/'app';app.mkdir(exist_ok=True)
    def info(python):
        return json.loads(subprocess.check_output([str(python),'-c','import sys,json;print(json.dumps(dict(base=sys.base_prefix,site=sys.prefix+"/Lib/site-packages",version=sys.version)))'],text=True))
    training=info(args.training_python);window=info(args.window_python);base=Path(training['base'])
    if not training['version'].startswith('3.13.') or not window['version'].startswith('3.13.'):
        raise SystemExit('This build recipe requires 64-bit Python 3.13 for both environments.')
    print('Copying independent Python runtime and training libraries...',flush=True)
    runtime.mkdir(exist_ok=True)
    for name in ['python.exe','pythonw.exe','python3.dll','python313.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt']:
        shutil.copy2(base/name,runtime/name)
    for name in ['DLLs','Lib','include','libs']:
        if name=='Lib':
            shutil.copytree(base/name,runtime/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns(*EXCLUDE,'site-packages','*.pyc','*.pyo'))
        else:copy_tree(base/name,runtime/name)
    site=runtime/'Lib/site-packages';copy_tree(Path(training['site']),site)
    installed={d.metadata['Name'].lower().replace('_','-') for d in importlib.metadata.distributions(path=[str(site)])}
    # Overlay only new GUI projects. Existing verified numerical dependencies stay intact.
    for dist in importlib.metadata.distributions(path=[window['site']]):
        if dist.metadata['Name'].lower().replace('_','-') in installed:continue
        for file in dist.files or []:
            relative=Path(str(file))
            if '..' in relative.parts or '__pycache__' in relative.parts or relative.suffix=='.pyc':continue
            src=Path(window['site'])/relative;dest=site/relative
            if src.is_file():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    (runtime/'python313._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n../app\nimport site\n',encoding='utf-8')
    shutil.copy2(SOURCE/'desktop/sitecustomize.py',site/'sitecustomize.py')
    for path in SOURCE.glob('*'):
        if path.is_file() and path.suffix in ('.py','.html','.txt') and path.name not in ('package.py','launch.py'):
            shutil.copy2(path,app/path.name)
    copy_tree(SOURCE/'desktop',app/'desktop')
    copy_tree(args.seed,app/'default_data')
    shutil.copy2(args.license,output/'LICENSE')
    shutil.copy2(base/'LICENSE.txt',output/'Python-LICENSE.txt')
    csc=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    exe=output/'魔杖手势训练.exe'
    subprocess.run([str(csc),'/nologo','/target:winexe','/platform:x64','/optimize+','/reference:System.Windows.Forms.dll','/win32icon:'+str(app/'desktop/wand.ico'),'/out:'+str(exe),str(SOURCE/'desktop/Launcher.cs')],check=True)
    # Microsoft's signed bootstrapper is only needed when WebView2 is absent.
    bootstrap=output/'显示组件安装.exe'
    if not bootstrap.exists():urllib.request.urlretrieve('https://go.microsoft.com/fwlink/p/?LinkId=2124703',bootstrap)
    (output/'使用说明.txt').write_text('''魔杖手势训练 · Windows 独立窗口版

双击“魔杖手势训练.exe”即可使用，无需安装 Python。
请完整解压并保留 app、runtime 文件夹，不要只复制 exe。

录制 → 检查数据 → 训练 → 测试 → 上传模型。
添加手势在录制页，每个手势至少120条不重复记录，最多16个。
采集、测试及上传时，电脑连接魔杖的 Wi-Fi。
电脑只管理手势和模型，不编译或更新系统固件。
旧魔杖首次需要升级6.0基础固件，之后只上传模型。
功能和灯效在魔杖自己的网页设置。

原有记录自动接续，数据保存在：
%LOCALAPPDATA%\\MugglesWandTrainer
更新或移动软件文件夹不会删除这些记录。

如提示缺少 WebView2，运行“显示组件安装.exe”联网安装后重试。
技术日志保存在上述数据目录的 desktop.log。

本软件基于 MIT 许可的 MugglesWand 项目修改：
https://github.com/zou-shui/MugglesWand
第三方组件许可保留在 runtime/Lib/site-packages 对应目录中。
''',encoding='utf-8-sig')
    source_hashes={str(p.relative_to(app)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in app.rglob('*') if p.is_file() and p.suffix in ('.py','.html','.h','.cpp','.cs')}
    manifest=dict(product='MugglesWandTrainer',version='1.1.0',build='20261105',architecture='windows-x64',python=training['version'],gestures_max=16,model_protocol=1,feature_contract=1,role='gesture-model-workbench',source_sha256=source_hashes,includes_user_data=False)
    (output/'build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    slim=prune_development_files(output)
    print('Runtime bytes '+str(slim['source_bytes'])+' -> '+str(slim['result_bytes']),flush=True)
    print('PRODUCT '+str(output),flush=True)
    if args.zip:
        archive=output.parent/(output.name+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
            for path in output.rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:z.write(path,Path(output.name)/path.relative_to(output))
        print('ARCHIVE '+str(archive),flush=True)


if __name__=='__main__':main()
