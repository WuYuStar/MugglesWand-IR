"""One-time local environment setup, then open the desktop trainer."""
import hashlib
import os
from pathlib import Path
import subprocess
import struct
import sys
import venv
import argparse
import webbrowser
from startup import default_workspace, find_running

HERE = Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--workspace',type=Path,default=default_workspace())
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args,_=parser.parse_known_args()
    existing=find_running(args.port,args.workspace)
    if existing:
        print('训练工具已在运行，正在打开原来的页面：'+existing,flush=True)
        if not args.no_browser:webbrowser.open(existing)
        return 0
    if not (3, 10) <= sys.version_info[:2] <= (3, 13):
        raise SystemExit('请安装 64 位 Python 3.10～3.13，然后重新启动。建议使用 Python 3.12。')
    if struct.calcsize('P') != 8:
        raise SystemExit('训练环境需要 64 位 Python，请安装后重试。')
    environment = HERE / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        print('正在准备本机运行环境…', flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = HERE / 'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / '.trainer-requirements'
    if not marker.is_file() or marker.read_text() != digest:
        print('首次启动需要联网下载训练环境（文件较大，请耐心等待）。以后训练可离线运行。', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(requirements)], check=True)
        marker.write_text(digest)
    return subprocess.call([str(python), str(HERE / 'app.py'), *sys.argv[1:]])

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError:
        print('安装没有完成。请确认可以上网，以及当前 Python 为支持的 64 位版本，然后重新启动。')
        raise SystemExit(1)
