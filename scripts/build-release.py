#!/usr/bin/env python3
"""Build a deterministic, allowlisted source release; never package credentials or local state."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'VERSION', 'LICENSE', 'README.md', 'install.sh', 'metadata.json', 'extension.js', 'prefs.js',
    'stylesheet.css', 'cal_bar.py', 'cal_bar_ctl.py', 'app_logic.py', 'companion_runtime.py',
    'companion_ui.py', 'companion.css', 'gnome_calendar.py', 'google_calendar.py',
    'schemas/org.gnome.shell.extensions.cal-bar.gschema.xml',
]


def build(output, epoch=0):
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('VERSION must contain a semantic version without the v prefix')
    output.mkdir(parents=True, exist_ok=True)
    prefix = f'cal-bar-v{version}'
    archive = output / f'{prefix}.tar.gz'
    with archive.open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as package:
            for filename in sorted(FILES):
                content = (ROOT / filename).read_bytes()
                info = tarfile.TarInfo(f'{prefix}/{filename}')
                info.size, info.mtime = len(content), epoch
                info.mode = 0o755 if filename == 'install.sh' else 0o644
                package.addfile(info, io.BytesIO(content))
    installer = output / 'install-cal-bar.sh'
    script = (ROOT / 'scripts/install-release.sh').read_text()
    if script.count('DEFAULT_VERSION=latest') != 1:
        raise ValueError('Installer version marker is missing or ambiguous')
    installer.write_text(script.replace('DEFAULT_VERSION=latest', f'DEFAULT_VERSION=v{version}'))
    installer.chmod(0o755)
    manifest = output / 'SHA256SUMS'
    manifest.write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                               for path in (archive, installer)))
    return archive, installer, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--epoch', type=int, default=0)
    args = parser.parse_args()
    for path in build(args.output, args.epoch):
        print(path)


if __name__ == '__main__':
    main()
