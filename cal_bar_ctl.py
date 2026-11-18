#!/usr/bin/python3
"""Start, stop, or restart the companion without restarting GNOME Shell."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from gi.repository import Gio, GLib
from companion_runtime import BUS_NAME, INTERFACE, OBJECT_PATH, ROOT, load_settings


def bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def running(connection):
    return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'NameHasOwner', GLib.Variant('(s)', (BUS_NAME,)), GLib.VariantType.new('(b)'),
        Gio.DBusCallFlags.NO_AUTO_START, 3000, None).unpack()[0]


def call(connection, method, parameters=None):
    result = connection.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters, None,
                                 Gio.DBusCallFlags.NO_AUTO_START, 3000, None)
    return result.unpack() if result else ()


def start_background(connection):
    log_dir = Path(GLib.get_user_state_dir()) / 'cal-bar'
    log_path = log_dir / 'companion.log'
    child = None
    if not running(connection):
        log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(log_path.with_suffix('.old.log'))
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        env = os.environ.copy()
        # Prefer the native display connection rather than a tmux pane's old Xauthority.
        if env.get('WAYLAND_DISPLAY'):
            env.setdefault('GDK_BACKEND', 'wayland')
        with os.fdopen(fd, 'a') as log:
            child = subprocess.Popen([sys.executable, str(ROOT / 'cal_bar.py'), '--background'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=env)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            if running(connection):
                call(connection, 'GetState')
                return
        except GLib.Error:
            pass
        if child and child.poll() not in (None, 0):
            break
        time.sleep(0.1)
    raise RuntimeError(f'Cal Bar could not start. See {log_path}')


def main(args=None):
    args = sys.argv[1:] if args is None else args
    command = args[0] if args else 'agenda'
    command = {'prefs': 'preferences', '--background': 'background'}.get(command, command)
    if command not in ('agenda', 'preferences', 'preview', 'refresh', 'background', 'restart', 'quit', 'stop', 'status', 'is-running'):
        raise RuntimeError('Usage: cal-bar [agenda|preferences|preview|refresh|restart|quit|status]')
    connection = bus()
    settings = load_settings()
    if command == 'is-running':
        return 0 if running(connection) else 1
    if command == 'status':
        state = json.loads(call(connection, 'GetState')[0]) if running(connection) else {'running': False}
        print(json.dumps(state))
        return 0
    if command == 'stop':
        if running(connection):
            call(connection, 'StopForRestart')
        return 0
    if command == 'quit':
        if running(connection):
            call(connection, 'Activate', GLib.Variant('(s)', ('quit',)))
        else:
            settings.set_boolean('app-enabled', False)
        Gio.Settings.sync()
        return 0
    if command == 'background' and not settings.get_boolean('app-enabled'):
        return 0
    settings.set_boolean('app-enabled', True)
    Gio.Settings.sync()
    if command == 'restart' and running(connection):
        start_background(connection)
        call(connection, 'StopForRestart')
        deadline = time.monotonic() + 5
        while running(connection) and time.monotonic() < deadline:
            time.sleep(0.1)
        if running(connection):
            raise RuntimeError('Cal Bar is still shutting down. Try restart again shortly.')
    start_background(connection)
    if command not in ('background', 'restart'):
        call(connection, 'Activate', GLib.Variant('(s)', (command,)))
    if command == 'restart':
        print('Cal Bar restarted. No logout needed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, GLib.Error) as error:
        print(f'Cal Bar: {error}', file=sys.stderr)
        sys.exit(1)
