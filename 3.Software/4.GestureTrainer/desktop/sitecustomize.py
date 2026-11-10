"""Portable interpreter startup: UTF-8 workers and normal script-local imports.

The _pth file deliberately excludes installed Python and PYTHONPATH. Restore
only the executing script's directory, which PlatformIO's esptool/SCons scripts
need to import their sibling modules, as they do under regular Python.
"""
from pathlib import Path
import sys

if sys.argv and sys.argv[0] not in ('','-c','-m'):
    script=Path(sys.argv[0])
    if script.is_file():
        directory=str(script.resolve().parent)
        if directory not in sys.path:sys.path.insert(0,directory)

for stream in (sys.stdin,sys.stdout,sys.stderr):
    if stream is not None and hasattr(stream,'reconfigure'):
        stream.reconfigure(encoding='utf-8',errors='replace')
