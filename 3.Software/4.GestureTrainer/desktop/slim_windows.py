"""Create an application runtime copy; retain numerical DLLs, APIs and licenses.

This is not a general-purpose Python development environment. Validate real
training, INT8 export/prediction and the native window before replacing a product.
"""
import argparse
import json
import os
from pathlib import Path
import shutil

# Headers/import libraries support compiling custom native extensions, which this
# fixed desktop product does not do. libclang is only used by developer tooling.
OMIT_DIRS={
    'runtime/include','runtime/libs',
    'runtime/Lib/site-packages/tensorflow/include',
    'runtime/Lib/site-packages/tensorflow/xla_aot_runtime_src',
    'runtime/Lib/site-packages/clang',
    'runtime/Lib/site-packages/platformio',
}

def size(root):
    return sum((Path(base)/name).stat().st_size for base,dirs,files in os.walk(root) for name in files)

def prune_development_files(root):
    """Use the same policy for future fresh builds; never target user data."""
    root=Path(root).resolve()
    if not (root/'runtime/python.exe').is_file():raise ValueError('Not a portable product directory.')
    before=size(root);omitted=[]
    for relative in sorted(OMIT_DIRS):
        path=root/relative
        if path.exists():shutil.rmtree(path);omitted.append(relative)
    for base,dirs,files in os.walk(root):
        for name in list(dirs):
            if name=='__pycache__':
                path=Path(base)/name;shutil.rmtree(path);dirs.remove(name);omitted.append(path.relative_to(root).as_posix())
        for name in files:
            path=Path(base)/name
            if path.suffix.lower() in ('.pyc','.pyo','.lib','.pdb'):
                path.unlink();omitted.append(path.relative_to(root).as_posix())
    result=dict(source_bytes=before,result_bytes=size(root),omitted=omitted,policy='training-runtime-v1',validation_required=True)
    (root/'runtime-slim-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

def slim_copy(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output==source or source in output.parents:raise ValueError('Use a separate output directory.')
    if output.exists():raise ValueError('Output must be a new directory.')
    omitted=[]
    def ignore(base,names):
        relative=Path(base).relative_to(source);skip=[]
        for name in names:
            path=Path(base)/name;key=(relative/name).as_posix()
            if name=='__pycache__' or key in OMIT_DIRS or (path.is_file() and path.suffix.lower() in ('.pyc','.pyo','.lib','.pdb')):
                skip.append(name);omitted.append(key)
        return skip
    before=size(source)
    shutil.copytree(source,output,ignore=ignore)
    result=dict(source_bytes=before,result_bytes=size(output),omitted=omitted,policy='training-runtime-v1',validation_required=True)
    (output/'runtime-slim-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(slim_copy(args.source,args.output),ensure_ascii=True))
