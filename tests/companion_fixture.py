"""Synthetic UI fixture; only run by run-companion-integration.sh in its isolated desktop."""
import json
import os
from pathlib import Path
import sys
import time
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk
from app_logic import Event
from cal_bar import CalBarApplication
import companion_ui

if os.environ.get('CAL_BAR_UI_TEST_ISOLATED') != '1':
    raise SystemExit('Run tests/run-companion-integration.sh')

ARTIFACT = Path(os.environ['XDG_STATE_HOME']) / 'cal-bar' / 'ui-test.json'


class FakeJob:
    cancelled = False

    def cancel(self):
        self.cancelled = True


def fake_calendars(_settings, _command, callback, *_):
    job = FakeJob()
    def finish():
        if not job.cancelled:
            callback({'calendars': [dict(id=uid, name=name, account=account, needsAttention=False)
                for uid, name, account in [('work', 'Work', 'work@example.invalid'),
                    ('shared', 'Team', 'work@example.invalid'), ('personal', 'Personal', 'home@example.invalid')]]}, None)
        return False
    GLib.idle_add(finish)
    return job


companion_ui.run_helper = fake_calendars


class Fixture(CalBarApplication):
    def __init__(self):
        super().__init__()
        try:
            self.results = json.loads(ARTIFACT.read_text())
        except (OSError, ValueError):
            self.results = {}
        self._tested = set()

    def do_startup(self):
        super().do_startup()
        self._probe_timer = GLib.timeout_add(300, self.probe)

    def sync(self):
        now = float(os.environ['CAL_BAR_TEST_START'])
        self.last_sync = time.time()
        self.events = [Event(id='work:planning:start', title='Planning with the team', start=now+1200, end=now+3000,
            calendar_name='Work', calendar_id='work', meeting_url='https://meet.google.com/test-fixture',
            event_url='https://calendar.google.com/calendar/event?id=fixture', uid='planning',
            recurrence_id='20261118T120000Z', can_respond=True, response_status='needsaction'),
            Event(id='personal:review:start', title='Project review', start=now+3600, end=now+5400,
            calendar_name='Personal', calendar_id='personal', meeting_url='https://example.zoom.us/j/123',
            native_id='personal:review', uid='review')]
        self.error = ''
        self.publish()

    def respond(self, event, response):
        self.results.setdefault('buttonReplies', []).append(event.response_payload(response))

    def probe(self):
        try:
            self.results.setdefault('diagnostics', {})[str(os.getpid())] = {
                'locked': self.locked, 'focusedRole': self.focused_role, 'agendaActive': self.agenda.is_active() if self.agenda else None,
                'preferencesActive': self.preferences.is_active() if self.preferences else None}
            if self.reminders:
                self.results['lastReminderState'] = [dict(title=w.get_title(), active=w.is_active(),
                    fullscreen=w.is_fullscreen(), width=w.get_width(), height=w.get_height()) for w in self.reminders]
            if self.agenda and self.agenda.get_visible() and 'agenda' not in self._tested:
                rows = []
                row = self.agenda.list.get_first_child()
                while row:
                    if isinstance(row, Gtk.Expander):
                        rows.append(row)
                    row = row.get_next_sibling()
                assert len(rows) == 2, 'Missing event submenus'
                rows[0].set_expanded(True)
                box = rows[0].get_child()
                child = box.get_first_child()
                while child:
                    if isinstance(child, Gtk.Box):
                        action = child.get_first_child()
                        while action:
                            if isinstance(action, Gtk.Button) and action.get_label() in ('Accept', 'Reject'):
                                action.emit('clicked')
                            action = action.get_next_sibling()
                    child = child.get_next_sibling()
                assert {p['response'] for p in self.results['buttonReplies']} == {'accepted', 'declined'}
                self.results['agenda'] = True
                self._tested.add('agenda')
            if self.preferences and self.preferences.get_visible() and len(self.preferences.rows) == 3 and 'preferences' not in self._tested:
                prefs = self.preferences
                prefs.all.set_active(False)
                prefs.rows[0][2].set_active(True)
                prefs.rows[1][2].set_active(True)
                prefs.rows[2][2].set_active(False)
                assert len(self.settings.get_strv('selected-calendars')) == 2
                self.results['preferences'] = True
                self._tested.add('preferences')
            if (self.reminders and all(w.get_width() > 1 for w in self.reminders) and
                    self.focused_role == 'reminder' and
                    'reminder' not in self._tested):
                assert len(self.reminders) == 2, 'Expected reminder and dimmer on two monitors'
                assert all(w.is_fullscreen() for w in self.reminders), 'Reminder windows are not fullscreen'
                self.results['reminderSizes'] = sorted([[w.get_width(), w.get_height()] for w in self.reminders])
                assert self.results['reminderSizes'] == [[1024, 768], [1280, 800]], self.results['reminderSizes']
                self.results['reminder'] = True
                self._tested.add('reminder')
                self.seen.mark(self.events[0])
                self.settings.set_int('reminder-minutes', 30)
                self.settings.set_boolean('reminders-enabled', True)
                self.dismiss_reminder()
                self.results['dedupId'] = self.events[0].id
            ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
            ARTIFACT.write_text(json.dumps(self.results))
        except Exception as error:
            self.results['error'] = repr(error)
            ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
            ARTIFACT.write_text(json.dumps(self.results))
        return not self.stopping

    def do_shutdown(self):
        GLib.source_remove(self._probe_timer)
        super().do_shutdown()


sys.exit(Fixture().run(sys.argv))
