#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_dir="${CAL_BAR_BIN_DIR:-$HOME/.local/bin}"
app_dir="$data_dir/cal-bar"
bridge_dir="$data_dir/gnome-shell/extensions/cal-bar@local"
bridge_changed=false
if ! cmp -s "$source_dir/extension.js" "$bridge_dir/extension.js" || \
   ! cmp -s "$source_dir/prefs.js" "$bridge_dir/prefs.js" || \
   ! cmp -s "$source_dir/metadata.json" "$bridge_dir/metadata.json"; then
    bridge_changed=true
fi
mkdir -p "$app_dir/schemas" "$bridge_dir/schemas" "$bin_dir" "$data_dir/applications" "$data_dir/dbus-1/services"
for file in cal_bar.py cal_bar_ctl.py app_logic.py companion_runtime.py companion_ui.py companion.css gnome_calendar.py google_calendar.py VERSION; do
    install -m 644 "$source_dir/$file" "$app_dir/$file"
done
for destination in "$app_dir" "$bridge_dir"; do
    install -m 644 "$source_dir/schemas/org.gnome.shell.extensions.cal-bar.gschema.xml" "$destination/schemas/"
    glib-compile-schemas --strict "$destination/schemas"
done
for file in metadata.json extension.js prefs.js stylesheet.css; do
    install -m 644 "$source_dir/$file" "$bridge_dir/$file"
done
/usr/bin/python3 - "$app_dir" "$bin_dir" "$data_dir" <<'PY'
from pathlib import Path
import shlex
import sys
app_dir, bin_dir, data_dir = map(Path, sys.argv[1:])
launcher = bin_dir / 'cal-bar'
launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(app_dir / 'cal_bar_ctl.py')) + ' "$@"\n')
launcher.chmod(0o755)
def desktop_arg(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
exec_line = '/usr/bin/python3 ' + desktop_arg(app_dir / 'cal_bar_ctl.py')
(data_dir / 'applications/io.github.calbar.Companion.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Cal Bar\nComment=Upcoming meetings and full-screen reminders\n'
    f'Exec={exec_line} agenda\nIcon=x-office-calendar\nTerminal=false\nCategories=Office;Calendar;\n'
    'StartupNotify=true\nStartupWMClass=io.github.calbar.Companion\n')
(data_dir / 'dbus-1/services/io.github.calbar.Companion.service').write_text(
    '[D-BUS Service]\nName=io.github.calbar.Companion\n'
    f'Exec=/usr/bin/python3 {desktop_arg(app_dir / "cal_bar.py")} --background\n')
PY
if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$data_dir/applications"
fi
if [ "${CAL_BAR_SKIP_RESTART:-0}" != 1 ] && "$bin_dir/cal-bar" is-running 2>/dev/null; then
    "$bin_dir/cal-bar" restart
fi
printf 'Installed Cal Bar companion to %s\nLauncher: %s/cal-bar\n' "$app_dir" "$bin_dir"
if "$bridge_changed"; then
    printf 'The GNOME bridge changed: log out and back in once to load it.\n'
else
    printf 'Companion updated. No logout needed.\n'
fi
printf 'Open settings: %s/cal-bar preferences\nFuture app updates: ./install.sh (restarts the app if running).\n' "$bin_dir"
