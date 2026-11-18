#!/usr/bin/env bash
# Real GTK windows and bridge, on a private D-Bus session and headless GNOME compositor.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${CAL_BAR_UI_TEST_ISOLATED:-}" != 1 ]; then
    test_root="$(mktemp -d /tmp/cal-bar-ui-test.XXXXXX)"
    export XDG_DATA_HOME="$test_root/data" XDG_CONFIG_HOME="$test_root/config"
    export XDG_CACHE_HOME="$test_root/cache" XDG_STATE_HOME="$test_root/state"
    export XDG_RUNTIME_DIR="$test_root/runtime"
    mkdir -m 700 "$XDG_RUNTIME_DIR"
    export CAL_BAR_BIN_DIR="$test_root/bin" CAL_BAR_UI_TEST_ISOLATED=1
    export CAL_BAR_TEST_START="$(date +%s)" ADW_DISABLE_PORTAL=1
    mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$CAL_BAR_BIN_DIR"
    CAL_BAR_SKIP_RESTART=1 "$repo_dir/install.sh"
    /usr/bin/python3 - "$XDG_DATA_HOME/cal-bar/cal_bar.py" "$repo_dir" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text('import sys,runpy\nsys.path.insert(0, ' + repr(sys.argv[2]) + ')\n' +
    'runpy.run_path(' + repr(str(Path(sys.argv[2]) / 'tests/companion_fixture.py')) + ', run_name="__main__")\n')
PY
    if [ "${CAL_BAR_TEST_DEBUG_BRIDGE:-0}" = 1 ]; then
        /usr/bin/python3 - "$XDG_DATA_HOME/gnome-shell/extensions/cal-bar@local/extension.js" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text().replace('_present(role) {', "_present(role) { console.log('UI_TEST PRESENT ' + role + ' ' + JSON.stringify(global.get_window_actors().map(a => [a.meta_window.get_title(), a.meta_window.get_wm_class(), a.meta_window.get_gtk_application_id(), a.is_mapped()])));")
s = s.replace('_arrange(window) {', "_arrange(window) { if (window.get_title()?.startsWith('Cal Bar')) console.log('UI_TEST ARRANGE ' + window.get_title() + ' ours=' + this._isOurs(window) + ' ready=' + this._readyWindows.has(window) + ' mapped=' + window.get_compositor_private()?.is_mapped());")
s = s.replace('Main.activateWindow(window);', "Main.activateWindow(window); console.log('UI_TEST FOCUS ' + window.get_title() + ' focused=' + global.display.focus_window?.get_title() + ' overview=' + Main.overview.visible + ' locked=' + Main.sessionMode.isLocked + ' time=' + global.get_current_time());")
p.write_text(s)
PY
    fi
    printf 'Integration artifacts: %s\n' "$test_root"
    exec dbus-run-session -- bash "$0"
fi
export WAYLAND_DISPLAY="cal-bar-ui-test-$$"
unset DISPLAY GNOME_SETUP_DISPLAY XAUTHORITY
gsettings set org.gnome.shell enabled-extensions "['cal-bar@local']"
gsettings set org.gnome.shell disabled-extensions "[]"
gsettings set org.gnome.shell disable-user-extensions false
gsettings --schemadir "$XDG_DATA_HOME/cal-bar/schemas" set org.gnome.shell.extensions.cal-bar reminders-enabled false
/usr/bin/python3 -c "from gi.repository import Gio, GLib; Gio.bus_own_name(Gio.BusType.SESSION, 'org.freedesktop.secrets', Gio.BusNameOwnerFlags.NONE, None, None, None); GLib.MainLoop().run()" &
secrets_pid=$!
gnome-shell --wayland --headless --virtual-monitor 1280x800 --virtual-monitor 1024x768 \
    --wayland-display "$WAYLAND_DISPLAY" --no-x11 --sm-disable --mode=user > "$XDG_STATE_HOME/shell.log" 2>&1 &
shell_pid=$!
trap '"$CAL_BAR_BIN_DIR/cal-bar" stop >/dev/null 2>&1 || true; kill "$shell_pid" "$secrets_pid" 2>/dev/null || true' EXIT
for _ in {1..60}; do
    if [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then break; fi
    sleep 0.25
done
"$CAL_BAR_BIN_DIR/cal-bar" background
sleep 6
"$CAL_BAR_BIN_DIR/cal-bar" status > "$XDG_STATE_HOME/before.json"
"$CAL_BAR_BIN_DIR/cal-bar" agenda
sleep 2
"$CAL_BAR_BIN_DIR/cal-bar" preferences
sleep 2
"$CAL_BAR_BIN_DIR/cal-bar" preview
sleep 3
"$CAL_BAR_BIN_DIR/cal-bar" restart
sleep 6
"$CAL_BAR_BIN_DIR/cal-bar" status > "$XDG_STATE_HOME/after.json"
kill -0 "$shell_pid"
# An ordinary installer update must replace/restart only the companion.
"$repo_dir/install.sh"
sleep 6
"$CAL_BAR_BIN_DIR/cal-bar" status > "$XDG_STATE_HOME/installed.json"
kill -0 "$shell_pid"
/usr/bin/python3 - "$XDG_STATE_HOME" <<'PY'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
before, after = [json.loads((root / name).read_text()) for name in ('before.json', 'after.json')]
assert before['bridgeConnected'] and after['bridgeConnected'], (before, after)
assert before['pid'] != after['pid'], 'Companion did not restart'
installed = json.loads((root / 'installed.json').read_text())
assert installed['pid'] != after['pid'] and installed['bridgeConnected'], 'Installer did not restart/reconnect the app'
state = json.loads((root / 'cal-bar/ui-test.json').read_text())
assert not state.get('error'), state
assert all(state.get(key) for key in ('agenda', 'preferences', 'reminder', 'reminderPersistsAfterStart')), state
seen = json.loads((root / 'cal-bar/reminders.json').read_text())
assert state['dedupId'] in seen, 'Reminder deduplication was lost on restart'
print('PASS: event submenus, reply buttons, multiple calendars, two fullscreen monitors, reminder persistence after meeting start, bridge reconnection, app restart, and installer update without a Shell restart')
PY
"$CAL_BAR_BIN_DIR/cal-bar" quit
sleep 1
if "$CAL_BAR_BIN_DIR/cal-bar" is-running; then echo 'FAIL: Quit left the app running'; exit 1; fi
if rg -q 'Traceback|JS ERROR|Cal Bar bridge:.*(TypeError|ReferenceError)' "$XDG_STATE_HOME/cal-bar/companion.log" "$XDG_STATE_HOME/shell.log"; then
    cat "$XDG_STATE_HOME/cal-bar/companion.log"
    exit 1
fi
printf 'PASS: Quit stops the app without restarting GNOME Shell\n'
