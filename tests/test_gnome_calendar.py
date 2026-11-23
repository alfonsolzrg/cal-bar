import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import gnome_calendar as gnome


class DiscoveryTests(unittest.TestCase):
    def test_multiple_accounts_disabled_sources_and_selections(self):
        def account(id, email, disabled=False, calendar=True):
            obj = Mock()
            obj.get_calendar.return_value = calendar
            obj.get_account.return_value = SimpleNamespace(props=SimpleNamespace(
                id=id, presentation_identity=email, calendar_disabled=disabled, attention_needed=False))
            return obj
        goa = Mock()
        goa.get_accounts.return_value = [account('a', 'work@example.com'), account('b', 'home@example.com'),
                                        account('disabled', 'disabled@example.com', True)]
        registry = Mock()
        sources = []
        for uid, aid in [('work', 'a'), ('shared', 'a'), ('home', 'b'), ('off', 'disabled'), ('local', None)]:
            source = Mock()
            source.get_uid.return_value = uid
            source.get_display_name.return_value = uid
            source.account_id = aid
            sources.append(source)
        registry.list_sources.return_value = sources
        def parent(source, ext):
            if source.account_id is None:
                return None
            value = Mock()
            value.get_extension.return_value.get_account_id.return_value = source.account_id
            return value
        registry.find_extension.side_effect = parent
        registry.check_enabled.return_value = True
        eds = SimpleNamespace(SOURCE_EXTENSION_CALENDAR='Calendar', SOURCE_EXTENSION_GOA='GNOME Online Accounts')
        calendars = gnome.discover_calendars(registry, goa, eds)
        self.assertEqual({info['id'] for _, info in calendars}, {'work', 'shared', 'home'})
        self.assertEqual(len(gnome.selected_calendars(calendars, None)), 3)
        self.assertEqual(len(gnome.selected_calendars(calendars, ['work', 'home'])), 2)
        self.assertEqual(gnome.selected_calendars(calendars, []), [])

    def test_gnome_query_and_recurrence_expansion_use_two_weeks(self):
        client = Mock()
        obj = Mock()
        obj.get_uid.return_value = 'invite'
        client.get_object_list_sync.return_value = (True, [obj])
        client.get_backend_property_sync.return_value = (False, '')
        ecal = SimpleNamespace(Client=Mock(), ClientSourceType=SimpleNamespace(EVENTS=0))
        ecal.Client.connect_sync.return_value = client
        ical = SimpleNamespace(Timezone=Mock())
        now = 1795003200  # 2026-11-18 12:00 UTC
        info = dict(id='work', name='Work', account='me@example.com', needsAttention=False)
        items, warnings = gnome.read_events([(Mock(), info)], ecal, Mock(), ical, now=now)
        self.assertEqual((items, warnings), ([], []))
        client.generate_instances_for_uid_sync.assert_called_once()
        args = client.generate_instances_for_uid_sync.call_args.args
        self.assertEqual(args[:3], ('invite', now, now + 14 * 86400))
        self.assertIn('20261202T120000Z', client.get_object_list_sync.call_args.args[0])


try:
    _, _, _, ICAL = gnome.load_libraries()
except RuntimeError:
    ICAL = None


@unittest.skipUnless(ICAL, 'Install GNOME calendar introspection packages to test real iCalendar conversion')
class ICalendarTests(unittest.TestCase):
    def convert(self, extra='', dates='DTSTART:20261118T120000Z\r\nDTEND:20261118T130000Z', source='work'):
        comp = ICAL.Component.new_from_string(
            f'BEGIN:VEVENT\r\nUID:shared-id\r\n{dates}\r\nSUMMARY:Planning\r\n{extra}\r\nEND:VEVENT\r\n')
        return gnome.instance_to_event(comp, comp.get_dtstart(), comp.get_dtend(),
            {'id': source, 'name': source}, {'me@example.com'}, ICAL)

    def test_no_status_and_cross_calendar_ids(self):
        event = self.convert()
        self.assertEqual(event['start']['dateTime'], '2026-11-18T12:00:00+00:00')
        self.assertNotEqual(event['id'], self.convert(source='personal')['id'])

    def test_declined_cancelled_and_all_day(self):
        self.assertIsNone(self.convert('ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com'))
        self.assertIsNone(self.convert('STATUS:CANCELLED'))
        self.assertIsNone(self.convert(dates='DTSTART;VALUE=DATE:20261118\r\nDTEND;VALUE=DATE:20261119'))
        self.assertIsNotNone(self.convert('ATTENDEE;PARTSTAT=DECLINED:mailto:someone@example.com'))

    def test_timezone_offsets_are_preserved(self):
        comp = ICAL.Component.new_from_string(
            'BEGIN:VEVENT\r\nUID:summer\r\nDTSTART:20260701T120000\r\nDTEND:20260701T130000\r\nEND:VEVENT\r\n')
        zone = ICAL.Timezone.get_builtin_timezone('America/New_York')
        start, end = comp.get_dtstart(), comp.get_dtend()
        start.set_timezone(zone)
        end.set_timezone(zone)
        event = gnome.instance_to_event(comp, start, end, {'id': 'work', 'name': 'Work'}, set(), ICAL)
        self.assertEqual(event['start']['dateTime'], '2026-07-01T16:00:00+00:00')

    def test_conference_url_and_fallback(self):
        self.assertEqual(self.convert('X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij')['meetingUrl'],
                         'https://meet.google.com/abc-defg-hij')
        self.assertEqual(self.convert('URL:https://calendar.google.com/event')['htmlLink'],
                         'https://calendar.google.com/event')
        self.assertEqual(self.convert('LOCATION:https://example.zoom.us/j/123')['meetingUrl'],
                         'https://example.zoom.us/j/123')

    def test_call_and_calendar_links_are_separate(self):
        event = self.convert('X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\r\nURL:https://calendar.google.com/calendar/event?id=123')
        self.assertEqual(event['meetingUrl'], 'https://meet.google.com/abc-defg-hij')
        self.assertEqual(event['htmlLink'], 'https://calendar.google.com/calendar/event?id=123')
        event = self.convert('URL:https://teams.microsoft.com/l/meetup-join/123')
        self.assertEqual(event['meetingUrl'], 'https://teams.microsoft.com/l/meetup-join/123')
        self.assertEqual(event['htmlLink'], '')
        self.assertEqual(self.convert('LOCATION:https://evilzoom.us/j/123')['meetingUrl'], '')

    def invitation(self):
        return ICAL.Component.new_from_string(
            'BEGIN:VEVENT\r\nUID:invite\r\nDTSTART:20261118T120000Z\r\nDTEND:20261118T130000Z\r\n'
            'ORGANIZER:mailto:organizer@example.com\r\n'
            'ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:me@example.com\r\n'
            'ATTENDEE;PARTSTAT=TENTATIVE:mailto:guest@example.com\r\nEND:VEVENT\r\n')

    def test_response_only_changes_my_attendee_and_preserves_organizer(self):
        comp = self.invitation()
        self.assertTrue(gnome.invitation_state(comp, {'me@example.com'}, ICAL)[0])
        gnome.set_response(comp, {'me@example.com'}, 'accepted', ICAL)
        text = comp.as_ical_string()
        self.assertIn('PARTSTAT=ACCEPTED;RSVP=FALSE:mailto:me@example.com', text)
        self.assertIn('PARTSTAT=TENTATIVE:mailto:guest@example.com', text)
        self.assertIn('ORGANIZER:mailto:organizer@example.com', text)
        gnome.set_response(comp, {'me@example.com'}, 'declined', ICAL)
        self.assertIn('PARTSTAT=DECLINED', comp.as_ical_string())

    def test_attendees(self):
        comp = ICAL.Component.new_from_string(self.invitation().as_ical_string().replace(
            'PARTSTAT=TENTATIVE:', 'PARTSTAT=TENTATIVE;CN=Guest Person:'))
        google = {'id': 'work', 'name': 'Work'}
        event = gnome.instance_to_event(comp, comp.get_dtstart(), comp.get_dtend(), google, {'me@example.com'}, ICAL)
        self.assertEqual([(a['email'], a['displayName'], a['responseStatus']) for a in event['attendees']],
                         [('me@example.com', '', 'needsaction'), ('guest@example.com', 'Guest Person', 'tentative')])

    def test_cannot_respond_for_other_accounts_or_organizer(self):
        with self.assertRaisesRegex(RuntimeError, 'Not an invitation'):
            gnome.set_response(self.invitation(), {'other@example.com'}, 'accepted', ICAL)
        comp = self.invitation()
        comp.get_first_property(ICAL.PropertyKind.ORGANIZER_PROPERTY).set_organizer('mailto:me@example.com')
        with self.assertRaisesRegex(RuntimeError, 'organize'):
            gnome.set_response(comp, {'me@example.com'}, 'declined', ICAL)
        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
            gnome.set_response(self.invitation(), {'me@example.com'}, 'invalid', ICAL)



if __name__ == '__main__':
    unittest.main()
