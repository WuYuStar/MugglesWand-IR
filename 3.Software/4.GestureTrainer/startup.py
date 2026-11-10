"""Small stdlib-only discovery shared by the launcher and local server."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

SERVICE = 'muggleswand-trainer'


def default_workspace():
    base=Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    return base/'MugglesWandTrainer'


def same_workspace(actual, expected):
    return isinstance(actual,str) and os.path.normcase(os.path.realpath(actual))==os.path.normcase(os.path.realpath(str(expected)))


def running_at(port, workspace):
    if type(port) is not int or not 1<=port<=65535:return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base=f'http://127.0.0.1:{port}'
    def get(path):
        with opener.open(base+path,timeout=1 if path=='/api/health' else 3) as response:
            return json.loads(response.read(2*1024*1024))
    try:
        info=get('/api/health')
        if info.get('service')==SERVICE and same_workspace(info.get('workspace'),workspace):return base
    except (OSError,ValueError,AttributeError):
        pass
    # Existing installations do not have /api/health. Verify both the fixed
    # gesture protocol and its data directory instead of trusting any web page.
    try:
        info=get('/api/workflow/state')
        if (info.get('profile')=='gesture-workflow-v1' and info.get('minimum')==120 and
            isinstance(info.get('gestures'),list) and info['gestures'][:8]==['a','e','n','o','p','s','8','∞'] and 8<=len(info['gestures'])<=16 and
            same_workspace(str(Path(info['workspace']).parent),workspace)):
            return base
    except (OSError,ValueError,AttributeError,KeyError,TypeError):
        pass
    return None


def find_running(port, workspace):
    ports=[port]
    try:
        info=json.loads((Path(workspace)/'server.json').read_text(encoding='utf-8'))
        if type(info.get('port')) is int and info['port'] not in ports:ports.insert(0,info['port'])
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    for candidate in ports:
        url=running_at(candidate,workspace)
        if url:return url
    return None


def remember_server(workspace, port):
    path=Path(workspace)/'server.json'
    pending=path.with_suffix('.pending')
    pending.write_text(json.dumps(dict(service=SERVICE,port=port)),encoding='utf-8')
    pending.replace(path)
