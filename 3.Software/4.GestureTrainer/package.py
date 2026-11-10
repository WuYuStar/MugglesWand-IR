"""Build a self-contained source distribution, excluding environments and build caches."""
import argparse
from pathlib import Path
import zipfile
from app import HERE, REPO, find_seed

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
args=parser.parse_args()
args.output.parent.mkdir(parents=True,exist_ok=True)
excluded={'.git','.pio','.vscode','.venv','__pycache__','firmware_template','default_data'}
with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in HERE.rglob('*'):
        relative=path.relative_to(HERE)
        if path.is_file() and not any(part in excluded for part in relative.parts):
            archive.write(path,Path('MugglesWandTrainer')/relative)
    for path in find_seed().glob('*.csv'):
        archive.write(path,Path('MugglesWandTrainer/default_data')/path.name)
    archive.write(REPO/'LICENSE','MugglesWandTrainer/LICENSE')
print(args.output.resolve())
