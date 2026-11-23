#!/usr/bin/python3
"""Restartable Cal Bar application. The Shell bridge contains no calendar UI."""
import json
import logging
import math
import os
import subprocess
import sys
import time
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from app_logic import Event, due_reminders, normalize_events, panel_state, safe_url
from companion_runtime import APP_VERSION, BUS_NAME, INTERFACE, OBJECT_PATH, ROOT, ReminderSound, SeenStore, load_settings, run_helper
from companion_ui import AgendaWindow, PreferencesWindow, TITLES, reminder_windows, time_label

CONTROL_XML = f'''<node><interface name="{INTERFACE}">
  <method name="GetState"><arg type="s" direction="out"/></method>
  <method name="Activate"><arg name="action" type="s" direction="in"/></method>
  <method name="SetSessionState"><arg name="context" type="s" direction="in"/></method>
  <method name="StopForRestart"/>
  <signal name="StateChanged"><arg type="s"/></signal>
  <signal name="Present"><arg type="s"/></signal>
</interface></node>'''


class CalBarApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=BUS_NAME, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        for option in ('background', 'preferences', 'preview', 'agenda'):
            self.add_main_option(option, 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, option, None)
        self.settings = load_settings()
        self.events = []
        self.error = ''
        self.agenda = None
        self.preferences = None
        self.reminder_event = None
        self.reminders = []
        self.reminder_countdown = None
        self.reminder_heading = None
        self.sync_job = None
        self.response_job = None
        self.response_busy = False
        self.last_sync = 0
        self.last_bridge = 0
        self.locked = True
        self.geometry = None
        self.focused_role = None
        self.stopping = False
        self.seen = SeenStore()
        self.sound = ReminderSound()
        self._state_json = ''
        self._bus_registration = 0
        self._lock_proxy = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()
        self.settings.set_boolean('app-enabled', True)
        self._settings_signal = self.settings.connect('changed', self._setting_changed)
        css = Gtk.CssProvider()
        css.load_from_path(str(ROOT / 'companion.css'))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        interface = Gio.DBusNodeInfo.new_for_xml(CONTROL_XML).interfaces[0]
        self._bus_registration = self.get_dbus_connection().register_object(OBJECT_PATH, interface, self._method_call, None, None)
        self._watch_lock()
        self._timer = GLib.timeout_add_seconds(5, self.tick)
        self.sync()
        self.publish()

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        if options.contains('preferences'):
            self.show_preferences()
        elif options.contains('preview'):
            self.preview()
        elif not options.contains('background'):
            self.show_agenda()
        return 0

    def _method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        try:
            args = parameters.unpack()
            if method == 'GetState':
                invocation.return_value(GLib.Variant('(s)', (self.state_json(),)))
                return
            if method == 'SetSessionState':
                context = json.loads(args[0])
                self.last_bridge = time.monotonic()
                self.set_locked(bool(context['locked']))
                self.geometry = context.get('monitor')
                self.focused_role = context.get('focusedRole')
                self.publish()
            elif method == 'Activate':
                actions = {'toggle-agenda': self.toggle_agenda, 'agenda': self.show_agenda,
                    'preferences': self.show_preferences, 'refresh': self.sync,
                    'preview': self.preview, 'restart': self.restart, 'quit': self.quit_app}
                action = actions.get(args[0])
                if not action:
                    raise ValueError('Unknown Cal Bar action')
                GLib.idle_add(lambda: (action(), False)[1])
            elif method == 'StopForRestart':
                GLib.idle_add(lambda: (self.stop(), False)[1])
            else:
                raise ValueError('Unknown control method')
            invocation.return_value(None)
        except (ValueError, TypeError, KeyError) as error:
            invocation.return_dbus_error(INTERFACE + '.InvalidRequest', str(error))

    def state_json(self):
        state = panel_state(self.events, time.time(), self.settings.get_int('reminder-minutes'),
                            self.error, self.settings.get_boolean('app-enabled'))
        state.update(version=1, appVersion=APP_VERSION, pid=os.getpid(), focusedRole=self.focused_role, bridgeConnected=time.monotonic() - self.last_bridge < 15)
        return json.dumps(state, ensure_ascii=False)

    def publish(self):
        if self.stopping or not self._bus_registration:
            return
        state = self.state_json()
        if state != self._state_json:
            self._state_json = state
            self.get_dbus_connection().emit_signal(None, OBJECT_PATH, INTERFACE,
                                                   'StateChanged', GLib.Variant('(s)', (state,)))

    def request_presentation(self, role):
        self.get_dbus_connection().emit_signal(None, OBJECT_PATH, INTERFACE, 'Present', GLib.Variant('(s)', (role,)))

    def _watch_lock(self):
        def ready(_source, result):
            try:
                self._lock_proxy = Gio.DBusProxy.new_for_bus_finish(result)
                self._lock_proxy.connect('g-signal', changed)
                self._lock_proxy.call('GetActive', None, Gio.DBusCallFlags.NONE, 3000, None, active)
            except GLib.Error:
                pass  # Fail closed until the Shell bridge supplies a known lock state.
        def changed(_proxy, _sender, signal, parameters):
            if signal == 'ActiveChanged':
                self.set_locked(parameters.unpack()[0])
        def active(proxy, result):
            try:
                self.set_locked(proxy.call_finish(result).unpack()[0])
            except GLib.Error:
                pass
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            'org.gnome.ScreenSaver', '/org/gnome/ScreenSaver', 'org.gnome.ScreenSaver', None, ready)

    def set_locked(self, locked):
        self.locked = locked
        if locked:
            self.dismiss_reminder()
            if self.agenda:
                self.agenda.set_visible(False)

    def _setting_changed(self, _settings, key):
        if key == 'app-enabled':
            if not self.settings.get_boolean(key):
                self.stop()
            return
        if key in ('connection-method', 'all-calendars', 'selected-calendars', 'credentials-path', 'calendar-id', 'account-revision'):
            if self.sync_job:
                self.sync_job.cancel()
                self.sync_job = None
            self.events = []
            self.dismiss_reminder()
            self.sync()
        elif key == 'reminder-sound-enabled':
            if not self.settings.get_boolean(key):
                self.sound.stop()
        elif key in ('reminders-enabled', 'reminder-minutes'):
            self.dismiss_reminder()
            self.tick()
        self.publish()

    def sync(self):
        if self.stopping or self.sync_job or self.response_busy:
            return
        self.last_sync = time.time()
        self.sync_job = run_helper(self.settings, 'events', self._synced)

    def _synced(self, data, error):
        self.sync_job = None
        if self.stopping:
            return
        if error:
            self.error = error
        else:
            self.events = normalize_events(data.get('items', []))
            self.error = '\n'.join(data.get('warnings', []))
        self.tick()
        if self.agenda:
            self.agenda.render()

    def tick(self):
        if self.stopping:
            return GLib.SOURCE_REMOVE
        now = time.time()
        if self.reminder_event:
            event = self.reminder_event
            # Preview reminders are not part of the calendar snapshot.
            if event.uid != 'preview' and not any(e.id == event.id for e in self.events):
                self.dismiss_reminder()
            elif self.reminder_countdown:
                if event.start > now:
                    minutes = math.ceil((event.start - now) / 60)
                    timing = f'Starts in {minutes} {"minute" if minutes == 1 else "minutes"}'
                    heading = 'YOUR MEETING IS COMING UP'
                else:
                    minutes = int((now - event.start) // 60)
                    timing = 'Started just now' if minutes == 0 else \
                        f'Started {minutes} {"minute" if minutes == 1 else "minutes"} ago'
                    heading = 'YOUR MEETING HAS STARTED'
                self.reminder_countdown.set_text(f'{time_label(event.start)} · {timing}')
                if self.reminder_heading:
                    self.reminder_heading.set_text(heading)
        if not self.locked and not self.reminders and self.settings.get_boolean('reminders-enabled'):
            due = due_reminders(self.events, now, self.settings.get_int('reminder-minutes'), self.seen.entries)
            if due:
                self.show_reminder(due[0])
        if now - self.last_sync >= 60:
            self.sync()
        self.publish()
        return GLib.SOURCE_CONTINUE

    def show_agenda(self):
        if self.stopping:
            return
        if self.agenda is None:
            self.agenda = AgendaWindow(self)
        self.agenda.render()
        self.agenda.present()
        self.request_presentation('agenda')

    def toggle_agenda(self):
        if self.agenda and self.agenda.get_visible():
            self.agenda.set_visible(False)
        else:
            self.show_agenda()

    def show_preferences(self):
        if self.agenda:
            self.agenda.set_visible(False)
        if self.preferences is None:
            self.preferences = PreferencesWindow(self)
        self.preferences.present()
        self.request_presentation('preferences')

    def preview(self):
        now = time.time()
        event = Event(id='preview', uid='preview', title='Your next meeting', start=now + 300, end=now + 3600)
        self.show_reminder(event, preview=True)

    def show_reminder(self, event, preview=False):
        if self.locked:
            self.notify('Unlock the desktop to preview a reminder.') if preview else None
            return
        self.dismiss_reminder()
        if self.agenda:
            self.agenda.set_visible(False)
        self.reminder_event = event
        self.reminders = reminder_windows(self, event, self.geometry)
        if not self.reminders:
            self.reminder_event = None
            return
        if not preview:
            try:
                self.seen.mark(event)
            except OSError:
                logging.warning('Could not persist reminder deduplication; retained in memory.')
        self.tick()
        self.request_presentation('reminder')
        if self.settings.get_boolean('reminder-sound-enabled'):
            self.sound.play(self.settings.get_string('reminder-melody'))

    def dismiss_reminder(self):
        self.sound.stop()
        windows, self.reminders = self.reminders, []
        self.reminder_event = None
        self.reminder_countdown = None
        self.reminder_heading = None
        for window in windows:
            window.destroy()

    def respond(self, event, response):
        if self.response_busy or not event.can_respond or self.stopping:
            return
        if self.sync_job:
            self.sync_job.cancel()
            self.sync_job = None
        self.response_busy = True
        if self.agenda:
            self.agenda.render()
        def finished(_data, error):
            self.response_job = None
            self.response_busy = False
            if self.stopping:
                return
            self.notify(f'Response failed: {error}' if error else
                        ('Invitation accepted' if response == 'accepted' else 'Invitation declined'))
            if not error and response == 'declined':
                self.events = [e for e in self.events if e.id != event.id]
            if self.agenda:
                self.agenda.render()
            self.publish()
            self.sync()
        self.response_job = run_helper(self.settings, 'respond', finished, event.response_payload(response))

    def notify(self, message):
        notification = Gio.Notification.new(message)  # the app name already says Cal Bar
        self.send_notification('cal-bar-status', notification)

    def open_url(self, url):
        if not safe_url(url):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error:
            self.notify('Could not open the meeting link in your browser.')

    def open_event(self, event):
        if safe_url(event.event_url):
            self.open_url(event.event_url)
        elif event.native_id:
            date = time.strftime('%Y-%m-%d', time.localtime(event.start))
            self.launch(['gnome-calendar', '--date', date, '--uuid', event.native_id])

    def launch(self, args):
        try:
            Gio.Subprocess.new(args, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.notify(f'Could not open {args[0]}. Check that it is installed.')

    def restart(self):
        subprocess.Popen([sys.executable, str(ROOT / 'cal_bar_ctl.py'), 'restart'], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def quit_app(self):
        self.settings.set_boolean('app-enabled', False)
        # Publish the hidden panel state before dropping the bus name.
        if self.stopping:
            return
        self.publish()
        self.stop()

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.quit()

    def do_shutdown(self):
        self.stopping = True
        if hasattr(self, '_timer'):
            GLib.source_remove(self._timer)
        if self.sync_job:
            self.sync_job.cancel()
        if self.response_job:
            self.response_job.cancel()
        if self.preferences:
            self.preferences.cleanup()
        self.dismiss_reminder()
        if self._bus_registration:
            self.get_dbus_connection().unregister_object(self._bus_registration)
        Adw.Application.do_shutdown(self)


def main():
    logging.basicConfig(level=logging.INFO, format='Cal Bar: %(message)s')
    app = CalBarApplication()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
