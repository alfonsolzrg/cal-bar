#!/usr/bin/python3
"""Isolated Google OAuth / Calendar helper. Secrets never go to stdout."""
import argparse
import base64
import hashlib
import json
import secrets
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from app_logic import LOOKAHEAD_DAYS

TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/calendar.events.readonly'


def request_json(url, data=None, token=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = urlencode(data).encode() if data is not None else None
    with urlopen(Request(url, data=body, headers=headers), timeout=25) as response:
        return json.load(response)


class Keyring:
    def __init__(self, client_id):
        import gi
        gi.require_version('Secret', '1')
        from gi.repository import Secret
        self.secret = Secret
        self.schema = Secret.Schema.new('org.gnome.shell.extensions.cal-bar',
            Secret.SchemaFlags.NONE, {'client': Secret.SchemaAttributeType.STRING})
        self.attributes = {'client': client_id}

    def load(self):
        value = self.secret.password_lookup_sync(self.schema, self.attributes, None)
        return json.loads(value) if value else None

    def save(self, token):
        if not self.secret.password_store_sync(self.schema, self.attributes,
                self.secret.COLLECTION_DEFAULT, 'Cal Bar Google Calendar',
                json.dumps(token), None):
            raise RuntimeError('Could not save Google credentials to the desktop keyring.')

    def clear(self):
        self.secret.password_clear_sync(self.schema, self.attributes, None)


def connect(client):
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            valid = parsed.path == '/callback' and secrets.compare_digest(query.get('state', [''])[0], state)
            if not valid:
                self.send_error(400, 'Invalid authorization callback')
                return
            result.update(query)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Cal Bar sign-in finished. You can close this tab.')

        def log_message(self, *args):
            pass

    with HTTPServer(('127.0.0.1', 0), Callback) as server:
        server.timeout = 1
        redirect = f'http://127.0.0.1:{server.server_port}/callback'
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': client['client_id'], 'redirect_uri': redirect,
            'response_type': 'code', 'scope': SCOPE, 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent',
        })
        if not webbrowser.open(url):
            raise RuntimeError('Could not open the browser for Google sign-in.')
        deadline = time.monotonic() + 180
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if 'code' not in result:
        raise RuntimeError('Google sign-in was cancelled or timed out. Try connecting again.')
    return request_json(TOKEN_URL, {
        'client_id': client['client_id'], 'client_secret': client.get('client_secret', ''),
        'code': result['code'][0], 'code_verifier': verifier,
        'redirect_uri': redirect, 'grant_type': 'authorization_code',
    })


def access_token(client, keyring):
    token = keyring.load()
    if not token:
        raise RuntimeError('Not connected. Open Cal Bar preferences and connect Google Calendar.')
    if token.get('expires_at', 0) <= time.time() + 60:
        if not token.get('refresh_token'):
            raise RuntimeError('Google authorization expired. Please reconnect in preferences.')
        fresh = request_json(TOKEN_URL, {
            'client_id': client['client_id'], 'client_secret': client.get('client_secret', ''),
            'refresh_token': token['refresh_token'], 'grant_type': 'refresh_token',
        })
        token.update(fresh)
        token['expires_at'] = time.time() + token.get('expires_in', 3600)
        keyring.save(token)
    return token['access_token']


def list_events(token, calendar_id):
    now = datetime.now(timezone.utc)
    params = {'timeMin': now.isoformat(), 'timeMax': (now + timedelta(days=LOOKAHEAD_DAYS)).isoformat(),
              'singleEvents': 'true', 'orderBy': 'startTime', 'maxResults': 250}
    items = []
    while True:
        page = request_json('https://www.googleapis.com/calendar/v3/calendars/' +
                            quote(calendar_id, safe='') + '/events?' + urlencode(params), token=token)
        items.extend(page.get('items', []))
        if not page.get('nextPageToken'):
            return items
        params['pageToken'] = page['nextPageToken']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['connect', 'disconnect', 'events'])
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--calendar', default='primary')
    args = parser.parse_args()
    try:
        client = json.loads(Path(args.credentials).expanduser().read_text()).get('installed')
        if not client or not client.get('client_id'):
            raise RuntimeError('Choose a Google OAuth Desktop app credentials JSON file.')
        keyring = Keyring(client['client_id'])
        if args.command == 'connect':
            token = connect(client)
            if not token.get('refresh_token'):
                raise RuntimeError('No refresh token received. Please reconnect and grant calendar access.')
            token['expires_at'] = time.time() + token.get('expires_in', 3600)
            keyring.save(token)
            result = {'ok': True}
        elif args.command == 'disconnect':
            keyring.clear()
            result = {'ok': True}
        else:
            result = {'items': list_events(access_token(client, keyring), args.calendar)}
    except HTTPError as error:
        result = {'error': f'Google returned HTTP {error.code}. Check calendar access or reconnect in preferences.'}
    except (URLError, TimeoutError):
        result = {'error': 'Cannot reach Google. Check your connection and refresh.'}
    except Exception as error:
        # Do not return arbitrary errors which might contain request bodies or secrets.
        result = {'error': str(error) if isinstance(error, RuntimeError) else
                  'Could not read credentials or access the desktop keyring. Check setup in README.'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
