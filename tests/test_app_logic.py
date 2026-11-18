import json
from pathlib import Path
import tempfile
import time
import unittest
from app_logic import (Event, due_reminders, meeting_status, normalize_events, panel_state,
                       safe_url, timestamp, visible_events)
from companion_runtime import HelperJob, SeenStore
from gi.repository import GLib


NOW = timestamp('2026-11-18T12:00:00Z')


def raw(uid, start='2026-11-18T12:05:00Z', end='2026-11-18T12:30:00Z', **extra):
    return dict(id=uid, start={'dateTime': start}, end={'dateTime': end}, **extra)


class SchedulingTests(unittest.TestCase):
    def test_recurring_instances_timezone_offsets_and_filtering(self):
        events = normalize_events([
            raw('repeat', start='2026-11-18T13:05:00+01:00', end='2026-11-18T13:30:00+01:00'),
            raw('repeat', start='2026-11-19T12:05:00Z', end='2026-11-19T12:30:00Z'),
            raw('cancelled', status='cancelled'),
            raw('declined', attendees=[{'self': True, 'responseStatus': 'declined'}]),
            {'id': 'all-day', 'start': {'date': '2026-11-18'}, 'end': {'date': '2026-11-19'}},
            raw('invalid', start='bad'), raw('backward', end='2026-11-18T11:00:00Z'),
            raw('floating', start='2026-11-18T12:05:00'),
        ])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].start, NOW + 300)
        self.assertNotEqual(events[0].id, events[1].id)

    def test_current_event_priority_expiration_and_panel_status(self):
        events = [Event('old', 'Old', NOW - 200, NOW), Event('live', 'Live', NOW - 100, NOW + 100),
                  Event('next', 'Next', NOW + 300, NOW + 600)]
        self.assertEqual(visible_events(events, NOW)[0].id, 'live')
        self.assertEqual(meeting_status(events[1], NOW, 5), 'current')
        self.assertEqual(meeting_status(events[2], NOW, 5), 'soon')
        self.assertEqual(meeting_status(events[2], NOW, 4), 'upcoming')
        self.assertEqual(panel_state(events, NOW, 5)['label'], 'Live · now')
        self.assertEqual(panel_state([], NOW, 5, error='offline')['icon'], 'dialog-warning-symbolic')
        self.assertFalse(panel_state(events, NOW, 5, running=False)['visible'])

    def test_reminder_boundary_deduplication_resume_and_disabled_setting(self):
        event = Event('next', 'Next', NOW + 300, NOW + 600)
        self.assertEqual(due_reminders([event], NOW - 1, 5, set()), [])
        self.assertEqual(due_reminders([event], NOW, 5, set()), [event])
        self.assertEqual(due_reminders([event], NOW + 200, 5, set()), [event])
        self.assertEqual(due_reminders([event], NOW, 5, {'next'}), [])
        self.assertEqual(due_reminders([event], event.start, 5, set()), [])
        self.assertEqual(due_reminders([event], NOW, 0, set()), [])

    def test_call_event_and_reply_identifiers_stay_separate(self):
        event, = normalize_events([raw('source:uid', meetingUrl='https://meet.google.com/abc',
            htmlLink='https://calendar.google.com/event?id=123', eventUid='uid', calendarId='source',
            recurrenceId='20261118T120500Z', nativeEventId='source:uid:20261118T120500Z', canRespond=True)])
        self.assertEqual(event.meeting_url, 'https://meet.google.com/abc')
        self.assertEqual(event.event_url, 'https://calendar.google.com/event?id=123')
        self.assertEqual(event.response_payload('declined'), {'calendarId': 'source', 'eventUid': 'uid',
            'recurrenceId': '20261118T120500Z', 'start': (NOW + 300) * 1000, 'response': 'declined'})
        with self.assertRaises(ValueError):
            event.response_payload('invalid')
        details, = normalize_events([raw('uid', htmlLink='https://calendar.google.com/event')])
        self.assertEqual(details.meeting_url, '')
        self.assertFalse(details.can_respond)

    def test_only_safe_https_links(self):
        for url in ['https://meet.google.com/abc', 'https://calendar.google.com/event?id=123']:
            self.assertTrue(safe_url(url))
        for url in ['javascript:alert(1)', 'file:///tmp/x', 'https://', 'https://a\nb',
                    'https://user@host', 'https://a\\b', 'https://a b']:
            self.assertFalse(safe_url(url))


class RuntimeTests(unittest.TestCase):
    def test_reminder_dismissal_survives_an_app_restart_without_saving_event_content(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'reminders.json'
            event = Event('calendar:uid:start', 'Private meeting title', time.time() + 60, time.time() + 3600)
            store = SeenStore(path)
            store.mark(event)
            restarted = SeenStore(path)
            self.assertIn(event.id, restarted.entries)
            self.assertNotIn(event.title, path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(due_reminders([event], time.time(), 5, restarted.entries), [])

    def test_corrupt_state_file_does_not_prevent_startup(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'reminders.json'
            path.write_text('invalid JSON')
            self.assertEqual(SeenStore(path).entries, {})

    def test_helper_receives_reply_payload_on_stdin(self):
        with tempfile.TemporaryDirectory() as root:
            helper = Path(root) / 'helper.py'
            helper.write_text('import json,sys\nprint(json.dumps({"received":json.load(sys.stdin)}))\n')
            result = []
            loop = GLib.MainLoop()
            timeout = GLib.timeout_add_seconds(3, lambda: (loop.quit(), False)[1])
            def finished(data, error):
                result.append((data, error))
                loop.quit()
            job = HelperJob([str(helper)], finished, {'response': 'accepted'})
            loop.run()
            GLib.source_remove(timeout)
            self.assertEqual(result, [({'received': {'response': 'accepted'}}, None)])
            self.assertEqual(job.timeout, 0)


if __name__ == '__main__':
    unittest.main()
