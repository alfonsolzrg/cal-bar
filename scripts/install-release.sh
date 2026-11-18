#!/usr/bin/env bash
# Downloaded releases pin DEFAULT_VERSION to the release that supplied this installer.
set -euo pipefail
REPOSITORY=romaad/cal-bar
DEFAULT_VERSION=latest

main() {
    local version="$DEFAULT_VERSION" install_deps=false temporary archive release_url cleanup_command
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --install-deps) install_deps=true ;;
            --help|-h)
                printf 'Usage: bash install-cal-bar.sh [vVERSION] [--install-deps]\n'
                printf 'Installs for your user. --install-deps uses sudo apt-get for system dependencies.\n'
                return ;;
            v[0-9]*) version="$1" ;;
            *) printf 'Unknown option: %s\n' "$1" >&2; return 1 ;;
        esac
        shift
    done
    if [ "$EUID" -eq 0 ]; then
        printf 'Run this installer as your normal desktop user, without sudo.\n' >&2
        return 1
    fi
    if [ "$(uname -s)" != Linux ]; then
        printf 'Cal Bar currently supports Linux with GNOME Shell 46.\n' >&2
        return 1
    fi
    command -v curl >/dev/null || { printf 'Install curl first.\n' >&2; return 1; }
    if "$install_deps"; then
        command -v apt-get >/dev/null || { printf 'Automatic dependencies require apt. See the README for manual setup.\n' >&2; return 1; }
        sudo apt-get update
        sudo apt-get install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-ecal-2.0 \
            gir1.2-edataserver-1.2 gir1.2-goa-1.0 libglib2.0-bin gnome-calendar gnome-control-center evolution-data-server gnome-online-accounts
    fi
    local shell_version
    shell_version="$(LC_ALL=C gnome-shell --version 2>/dev/null || true)"
    if [[ ! "$shell_version" =~ ^GNOME\ Shell\ 46([.]|$) ]]; then
        printf 'This release supports GNOME Shell 46 (tested on Ubuntu 24.04). Detected: %s\n' "${shell_version:-not installed}" >&2
        return 1
    fi
    command -v glib-compile-schemas >/dev/null || { printf 'Install libglib2.0-bin first, or pass --install-deps.\n' >&2; return 1; }
    /usr/bin/python3 - <<'PY'
import sys
try:
    import gi
    from importlib import import_module
    for name, version in [('Gtk', '4.0'), ('Adw', '1'), ('EDataServer', '1.2'), ('ECal', '2.0'), ('Goa', '1.0')]:
        gi.require_version(name, version)
        import_module('gi.repository.' + name)
except (ImportError, ValueError):
    sys.exit('Missing GNOME calendar bindings. Run this installer with --install-deps, or see the README.')
PY
    temporary="$(mktemp -d)"
    printf -v cleanup_command 'rm -rf -- %q' "$temporary"
    trap "$cleanup_command" EXIT
    if [ "$version" = latest ]; then
        curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            "https://api.github.com/repos/$REPOSITORY/releases/latest" -o "$temporary/release.json"
        version="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$temporary/release.json")"
    fi
    [[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || { printf 'Invalid release version.\n' >&2; return 1; }
    archive="cal-bar-$version.tar.gz"
    release_url="https://github.com/$REPOSITORY/releases/download/$version"
    printf 'Downloading Cal Bar %s…\n' "$version"
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$release_url/$archive" -o "$temporary/$archive"
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$release_url/SHA256SUMS" -o "$temporary/SHA256SUMS"
    /usr/bin/python3 - "$temporary" "$archive" "cal-bar-$version" <<'PY'
import hashlib
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
root, filename, prefix = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
checksums = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    if not re.fullmatch('[0-9a-f]{64}', digest) or name in checksums:
        sys.exit('Invalid release checksum manifest.')
    checksums[name] = digest
archive = root / filename
if checksums.get(filename) != hashlib.sha256(archive.read_bytes()).hexdigest():
    sys.exit('Release checksum verification failed; nothing was installed.')
with tarfile.open(archive, 'r:gz') as package:
    for member in package.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != prefix:
            sys.exit('Unexpected path in release archive.')
        if not (member.isdir() or member.isfile()):
            sys.exit('Unexpected link or special file in release archive.')
    package.extractall(root, filter='data')
print('Release checksum verified.')
PY
    bash "$temporary/cal-bar-$version/install.sh"
    printf '\nInstalled Cal Bar %s. Open Cal Bar from Applications after any required one-time bridge reload.\n' "$version"
}

main "$@"
