"""Sample meetings for documentation captures, never connected to real accounts."""
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from app_logic import Event
from cal_bar import CalBarApplication

if os.environ.get('CAL_BAR_DOCS_ISOLATED') != '1':
    raise SystemExit('Run scripts/capture-docs.sh to use a private desktop session.')


class ScreenshotFixture(CalBarApplication):
    def sync(self):
        now = float(os.environ['CAL_BAR_DOCS_START'])
        self.last_sync = time.time()
        self.events = [Event(id='demo:design-review', title='Design review', start=now+300, end=now+2100,
            calendar_name='Work', calendar_id='demo-work', meeting_url='https://meet.google.com/example',
            event_url='https://calendar.google.com/calendar/event?id=example', uid='demo-design',
            can_respond=True, response_status='needsaction'),
            Event(id='demo:catch-up', title='Project catch-up', start=now+3600, end=now+5400,
            calendar_name='Team', calendar_id='demo-team', meeting_url='https://example.zoom.us/j/123',
            native_id='demo-team:catch-up', uid='demo-catch-up')]
        self.error = ''
        self.publish()

    def show_agenda(self):
        super().show_agenda()
        row = self.agenda.list.get_first_child()
        if isinstance(row, Gtk.Expander):
            row.set_expanded(True)

    def preview(self):
        self.show_reminder(self.events[0], preview=True)

    def respond(self, _event, _response):
        # The demo deliberately never sends invitation replies.
        pass


sys.exit(ScreenshotFixture().run(sys.argv))
