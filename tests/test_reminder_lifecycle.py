from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from app_logic import Event, normalize_events
from cal_bar import CalBarApplication


class ReminderLifecycleTests(unittest.TestCase):
    def app(self, event):
        settings = Mock()
        settings.get_boolean.return_value = False
        return SimpleNamespace(stopping=False, reminder_event=event, events=[event],
            reminders=[Mock()], reminder_countdown=Mock(), reminder_heading=Mock(),
            settings=settings, locked=False, last_sync=1000, seen=SimpleNamespace(entries={}),
            dismiss_reminder=Mock(), publish=Mock(), sync=Mock())

    def test_reminder_stays_open_across_the_meeting_start(self):
        event = Event('invite', 'Design review', 1000, 2800, uid='invite')
        app = self.app(event)
        for now, timing, heading in [(940, 'Starts in 1 minute', 'YOUR MEETING IS COMING UP'),
                (1000, 'Started just now', 'YOUR MEETING HAS STARTED'),
                (1001, 'Started just now', 'YOUR MEETING HAS STARTED'),
                (1060, 'Started 1 minute ago', 'YOUR MEETING HAS STARTED'),
                (1120, 'Started 2 minutes ago', 'YOUR MEETING HAS STARTED')]:
            with self.subTest(now=now), patch('cal_bar.time.time', return_value=now):
                CalBarApplication.tick(app)
                app.dismiss_reminder.assert_not_called()
                self.assertTrue(app.reminder_countdown.set_text.call_args.args[0].endswith(timing))
                app.reminder_heading.set_text.assert_called_with(heading)

    def test_polling_an_ongoing_meeting_preserves_the_open_reminder(self):
        raw = {'id': 'invite', 'eventUid': 'invite', 'summary': 'Design review',
               'start': {'dateTime': '2026-11-18T12:00:00Z'},
               'end': {'dateTime': '2026-11-18T12:30:00Z'}}
        event, = normalize_events([raw])
        app = self.app(event)
        app.agenda = None
        app.tick = lambda: CalBarApplication.tick(app)
        with patch('cal_bar.time.time', return_value=event.start + 61):
            CalBarApplication._synced(app, {'items': [raw]}, None)
        app.dismiss_reminder.assert_not_called()
        self.assertEqual(app.reminder_event, event)

    def test_cancelled_or_removed_events_still_close_their_reminder(self):
        event = Event('invite', 'Design review', 1000, 2800, uid='invite')
        app = self.app(event)
        app.events = []
        with patch('cal_bar.time.time', return_value=1001):
            CalBarApplication.tick(app)
        app.dismiss_reminder.assert_called_once()

    def test_preview_does_not_disappear_at_its_start_time(self):
        event = Event('preview', 'Preview', 1000, 2800, uid='preview')
        app = self.app(event)
        app.events = []
        with patch('cal_bar.time.time', return_value=1001):
            CalBarApplication.tick(app)
        app.dismiss_reminder.assert_not_called()

    def test_explicit_dismissal_still_destroys_all_reminder_windows(self):
        app = self.app(Event('invite', 'Design review', 1000, 2800, uid='invite'))
        windows = list(app.reminders)
        CalBarApplication.dismiss_reminder(app)
        for window in windows:
            window.destroy.assert_called_once()
        self.assertEqual(app.reminders, [])
        self.assertIsNone(app.reminder_event)
        self.assertIsNone(app.reminder_countdown)
        self.assertIsNone(app.reminder_heading)


if __name__ == '__main__':
    unittest.main()
