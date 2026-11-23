"""Settings, durable reminder deduplication, and isolated calendar subprocesses."""
import json
import logging
import os
from pathlib import Path
import sys
import time
from reminder_melodies import render_melody
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


class ReminderSound:
    """Play a selected melody through GNOME's libcanberra player."""
    def __init__(self, desktop_settings=None):
        self.process = None
        self.desktop = desktop_settings
        if self.desktop is None:
            schema = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.desktop.sound', True)
            if schema:
                self.desktop = Gio.Settings.new_full(schema, None, None)

    def play(self, melody=None):
        self.stop()
        if self.desktop and not self.desktop.get_boolean('event-sounds'):
            return
        executable = GLib.find_program_in_path('canberra-gtk-play')
        if not executable:
            logging.warning('Reminder sound unavailable: install gnome-session-canberra and sound-theme-freedesktop.')
            return
        sound_arg = '--id=alarm-clock-elapsed'
        if melody is not None:
            try:
                sound_arg = '--file=' + str(render_melody(melody, Path(GLib.get_user_cache_dir()) / 'cal-bar'))
            except OSError as error:
                logging.warning('Could not render melody: %s', error)
        args = [executable, sound_arg, '--description=Cal Bar meeting reminder',
                '--property=media.role=event', f'--property=application.id={BUS_NAME}',
                '--property=application.name=Cal Bar', '--loop=1']
        if self.desktop:
            theme = self.desktop.get_string('theme-name')
            if theme:
                args.append(f'--property=canberra.xdg-theme.name={theme}')
        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
            if GLib.getenv('WAYLAND_DISPLAY'):
                launcher.setenv('GDK_BACKEND', 'wayland', True)
            process = launcher.spawnv(args)
            self.process = process
            process.wait_async(None, self._finished)
        except GLib.Error as error:
            self.process = None
            logging.warning('Could not play reminder sound: %s', error.message)

    def _finished(self, process, result):
        try:
            process.wait_finish(result)
            if self.process is process and not process.get_successful():
                logging.warning('Reminder sound could not be played. Check the desktop audio settings.')
        except GLib.Error as error:
            logging.warning('Reminder sound failed: %s', error.message)
        finally:
            if self.process is process:
                self.process = None

    def stop(self):
        process, self.process = self.process, None
        if process:
            process.force_exit()


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
