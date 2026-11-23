#!/usr/bin/env bash
# Capture real app windows with sample data on a private GNOME desktop.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${CAL_BAR_DOCS_ISOLATED:-}" != 1 ]; then
    export CAL_BAR_DOCS_OUTPUT="$(/usr/bin/python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${1:-$repo_dir/docs/screenshots}")"
    mkdir -p "$CAL_BAR_DOCS_OUTPUT"
    glib-compile-schemas --strict "$repo_dir/schemas"
    docs_root="$(mktemp -d /tmp/cal-bar-docs.XXXXXX)"
    export XDG_DATA_HOME="$docs_root/data" XDG_CONFIG_HOME="$docs_root/config"
    export XDG_CACHE_HOME="$docs_root/cache" XDG_STATE_HOME="$docs_root/state" XDG_RUNTIME_DIR="$docs_root/runtime"
    export CAL_BAR_BIN_DIR="$docs_root/bin" CAL_BAR_DOCS_ISOLATED=1 CAL_BAR_DOCS_START="$(date +%s)" ADW_DISABLE_PORTAL=1
    mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$CAL_BAR_BIN_DIR"
    mkdir -m 700 "$XDG_RUNTIME_DIR"
    CAL_BAR_SKIP_RESTART=1 "$repo_dir/install.sh"
    /usr/bin/python3 - "$XDG_DATA_HOME/cal-bar/cal_bar.py" "$repo_dir/scripts/screenshot-fixture.py" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text('import runpy\nrunpy.run_path(' + repr(sys.argv[2]) + ', run_name="__main__")\n')
PY
    printf 'Private screenshot session: %s\n' "$docs_root"
    exec dbus-run-session -- bash "$0"
fi
export WAYLAND_DISPLAY="cal-bar-docs-$$"
unset DISPLAY GNOME_SETUP_DISPLAY XAUTHORITY
gsettings set org.gnome.shell enabled-extensions "['cal-bar@local']"
gsettings set org.gnome.shell disabled-extensions "[]"
gsettings set org.gnome.shell disable-user-extensions false
gsettings set org.gnome.desktop.background picture-uri ''
gsettings set org.gnome.desktop.background picture-uri-dark ''
gsettings set org.gnome.desktop.background primary-color '#182233'
gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark'
gsettings --schemadir "$XDG_DATA_HOME/cal-bar/schemas" set org.gnome.shell.extensions.cal-bar reminders-enabled false
gsettings --schemadir "$XDG_DATA_HOME/cal-bar/schemas" set org.gnome.shell.extensions.cal-bar reminder-sound-enabled false
/usr/bin/python3 -c "from gi.repository import Gio, GLib; Gio.bus_own_name(Gio.BusType.SESSION, 'org.freedesktop.secrets', Gio.BusNameOwnerFlags.NONE, None, None, None); GLib.MainLoop().run()" &
secrets_pid=$!
sm_flag=()  # --sm-disable was removed after GNOME 46
gnome-shell --help 2>&1 | grep -q -- --sm-disable && sm_flag=(--sm-disable)
gnome-shell --wayland --headless --virtual-monitor 1280x800 --wayland-display "$WAYLAND_DISPLAY" \
    --no-x11 "${sm_flag[@]}" --mode=user > "$XDG_STATE_HOME/shell.log" 2>&1 &
shell_pid=$!
trap '"$CAL_BAR_BIN_DIR/cal-bar" stop >/dev/null 2>&1 || true; kill "$shell_pid" "$secrets_pid" 2>/dev/null || true' EXIT
for _ in {1..60}; do
    if [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then break; fi
    sleep 0.25
done
"$CAL_BAR_BIN_DIR/cal-bar" background
sleep 6
"$CAL_BAR_BIN_DIR/cal-bar" agenda
sleep 2
/usr/bin/python3 "$repo_dir/scripts/capture-screenshot.py" "$CAL_BAR_DOCS_OUTPUT/meeting-info.png"
"$CAL_BAR_BIN_DIR/cal-bar" preview
sleep 2
/usr/bin/python3 "$repo_dir/scripts/capture-screenshot.py" "$CAL_BAR_DOCS_OUTPUT/reminder.png"
printf 'Documentation screenshots saved to %s\n' "$CAL_BAR_DOCS_OUTPUT"
