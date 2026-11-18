import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import google_calendar as google


class FakeKeyring:
    def __init__(self, token):
        self.token = token

    def load(self):
        return self.token

    def save(self, token):
        self.token = token


class CalendarTests(unittest.TestCase):
    def test_refresh_keeps_refresh_token(self):
        keyring = FakeKeyring({'refresh_token': 'refresh', 'expires_at': 0})
        with patch.object(google, 'request_json', return_value={'access_token': 'access', 'expires_in': 3600}):
            self.assertEqual(google.access_token({'client_id': 'client'}, keyring), 'access')
        self.assertEqual(keyring.token['refresh_token'], 'refresh')

    def test_pagination_and_calendar_id_encoding(self):
        with patch.object(google, 'request_json', side_effect=[
            {'items': [{'id': 'a'}], 'nextPageToken': 'page2'}, {'items': [{'id': 'b'}]},
        ]) as request:
            self.assertEqual(google.list_events('token', 'shared@example.com'), [{'id': 'a'}, {'id': 'b'}])
            self.assertIn('shared%40example.com/events?', request.call_args_list[0].args[0])
            self.assertIn('pageToken=page2', request.call_args_list[1].args[0])
            query = parse_qs(urlsplit(request.call_args_list[0].args[0]).query)
            start = datetime.fromisoformat(query['timeMin'][0])
            end = datetime.fromisoformat(query['timeMax'][0])
            self.assertEqual((end - start).total_seconds(), 14 * 86400)

    def test_disconnected_never_requests_google(self):
        with patch.object(google, 'request_json') as request:
            with self.assertRaisesRegex(RuntimeError, 'Not connected'):
                google.access_token({'client_id': 'client'}, FakeKeyring(None))
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
