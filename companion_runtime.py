"""Settings, durable reminder deduplication, and isolated calendar subprocesses."""
import json
import os
from pathlib import Path
import sys
import time
from gi.repository import Gio, GLib

BUS_NAME = 'io.github.calbar.Companion'
OBJECT_PATH = '/io/github/calbar/Companion/Control'
INTERFACE = BUS_NAME + '.Control'
ROOT = Path(__file__).resolve().parent
try:
    APP_VERSION = (ROOT / 'VERSION').read_text().strip()
except OSError:
    APP_VERSION = 'development'


def load_settings():
    source = Gio.SettingsSchemaSource.new_from_directory(str(ROOT / 'schemas'),
        Gio.SettingsSchemaSource.get_default(), False)
    return Gio.Settings.new_full(source.lookup('org.gnome.shell.extensions.cal-bar', False), None, None)


class SeenStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(GLib.get_user_state_dir()) / 'cal-bar' / 'reminders.json'
        try:
            data = json.loads(self.path.read_text())
            self.entries = {key: float(end) for key, end in data.items() if float(end) > time.time()}
        except (OSError, ValueError, TypeError, AttributeError):
            self.entries = {}

    def mark(self, event):
        self.entries = {key: end for key, end in self.entries.items() if end > time.time()}
        self.entries[event.id] = event.end
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(self.entries, stream)
        temp.replace(self.path)


class HelperJob:
    def __init__(self, args, callback, payload=None):
        self.cancelled = False
        self.timed_out = False
        self.callback = callback
        self.cancellable = Gio.Cancellable()
        self.process = Gio.Subprocess.new([sys.executable, *args], Gio.SubprocessFlags.STDIN_PIPE |
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        self.timeout = GLib.timeout_add_seconds(90, self._timeout)
        self.process.communicate_utf8_async(json.dumps(payload) if payload is not None else None,
            self.cancellable, self._finish)

    def _timeout(self):
        self.timeout = 0
        self.timed_out = True
        self.process.force_exit()
        return GLib.SOURCE_REMOVE

    def _finish(self, process, result):
        if self.timeout:
            GLib.source_remove(self.timeout)
            self.timeout = 0
        try:
            _, stdout, _ = process.communicate_utf8_finish(result)
            if self.cancelled:
                return
            if self.timed_out:
                raise RuntimeError('Calendar operation timed out. Check GNOME Online Accounts.')
            if not process.get_successful():
                raise RuntimeError('The calendar helper could not run. Check the installation.')
            data = json.loads(stdout)
            if data.get('error'):
                raise RuntimeError(data['error'])
        except Exception as error:
            if not self.cancelled:
                self.callback(None, str(error))
        else:
            self.callback(data, None)

    def cancel(self):
        self.cancelled = True
        self.cancellable.cancel()
        self.process.force_exit()
        if self.timeout:
            GLib.source_remove(self.timeout)
            self.timeout = 0


def run_helper(settings, command, callback, payload=None):
    if command != 'calendars' and settings.get_string('connection-method') == 'direct':
        if command == 'respond':
            GLib.idle_add(lambda: (callback(None, 'Direct OAuth is read-only. Respond in Calendar.'), False)[1])
            return None
        credentials = settings.get_string('credentials-path')
        args = [str(ROOT / 'google_calendar.py'), command, '--credentials', credentials,
                '--calendar', settings.get_string('calendar-id')]
    else:
        args = [str(ROOT / 'gnome_calendar.py'), command]
        if not settings.get_boolean('all-calendars'):
            args += ['--sources', json.dumps(settings.get_strv('selected-calendars'))]
    try:
        return HelperJob(args, callback, payload)
    except Exception:
        GLib.idle_add(lambda: (callback(None, 'Could not start the calendar helper. Check Python dependencies.'), False)[1])
        return None
