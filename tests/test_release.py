import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts/build-release.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ReleaseTests(unittest.TestCase):
    def test_archive_is_reproducible_and_contains_only_runtime_files(self):
        with tempfile.TemporaryDirectory() as root:
            first = BUILDER.build(Path(root) / 'first')
            second = BUILDER.build(Path(root) / 'second')
            self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])
            version = (ROOT / 'VERSION').read_text().strip()
            self.assertIn(f'DEFAULT_VERSION=v{version}', first[1].read_text())
            with tarfile.open(first[0]) as archive:
                names = {member.name for member in archive.getmembers()}
                self.assertEqual(names, {f'cal-bar-v{version}/{name}' for name in BUILDER.FILES})
                self.assertFalse(any('.git' in name or '__pycache__' in name or 'credentials' in name for name in names))
            for line in first[2].read_text().splitlines():
                digest, name = line.split()
                self.assertEqual(digest, hashlib.sha256((first[2].parent / name).read_bytes()).hexdigest())

    def run_installer(self, directory, corrupt=False, shell_version='46.0'):
        assets = directory / 'assets'
        archive, installer, manifest = BUILDER.build(assets)
        if corrupt:
            archive.write_bytes(archive.read_bytes() + b'corrupted')
        binary = directory / 'bin'
        binary.mkdir()
        shell = binary / 'gnome-shell'
        shell.write_text(f'#!/bin/sh\nprintf "GNOME Shell {shell_version}\\n"\n')
        shell.chmod(0o755)
        curl = binary / 'curl'
        curl.write_text('''#!/usr/bin/python3
import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith('https://'))
output = args[args.index('-o') + 1]
shutil.copyfile(Path(os.environ['TEST_RELEASE_ASSETS']) / url.rsplit('/', 1)[1], output)
''')
        curl.chmod(0o755)
        environment = os.environ.copy()
        environment.update(PATH=f'{binary}:{environment["PATH"]}',
            XDG_DATA_HOME=str(directory / 'data'), XDG_CONFIG_HOME=str(directory / 'config'),
            XDG_STATE_HOME=str(directory / 'state'), CAL_BAR_BIN_DIR=str(directory / 'launchers'),
            CAL_BAR_SKIP_RESTART='1', TEST_RELEASE_ASSETS=str(assets), TMPDIR=str(directory))
        result = subprocess.run(['bash', str(installer)], env=environment, capture_output=True, text=True)
        return result

    @unittest.skipIf(os.geteuid() == 0, 'Per-user installer deliberately refuses root')
    def test_downloaded_installer_installs_verified_release_without_touching_live_app(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            result = self.run_installer(directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Release checksum verified.', result.stdout)
            self.assertEqual((directory / 'data/cal-bar/VERSION').read_text(), (ROOT / 'VERSION').read_text())
            self.assertTrue((directory / 'data/gnome-shell/extensions/cal-bar@local/extension.js').exists())
            self.assertTrue((directory / 'launchers/cal-bar').stat().st_mode & 0o111)
            self.assertFalse(list(directory.glob('tmp.*')), 'Installer did not remove its temporary directory')

    @unittest.skipIf(os.geteuid() == 0, 'Per-user installer deliberately refuses root')
    def test_checksum_failure_installs_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            result = self.run_installer(directory, corrupt=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('checksum verification failed', result.stderr)
            self.assertFalse((directory / 'data/cal-bar').exists())
            self.assertFalse(list(directory.glob('tmp.*')))

    @unittest.skipIf(os.geteuid() == 0, 'Per-user installer deliberately refuses root')
    def test_unsupported_gnome_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            result = self.run_installer(directory, shell_version='47.0')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('supports GNOME Shell 46', result.stderr)
            self.assertFalse((directory / 'data/cal-bar').exists())


if __name__ == '__main__':
    unittest.main()
