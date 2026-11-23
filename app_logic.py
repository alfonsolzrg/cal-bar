"""Calendar-independent scheduling and panel state for the companion app."""
from dataclasses import dataclass
from datetime import datetime
import math
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

LOOKAHEAD_DAYS = 14
LOOKAHEAD_SECONDS = LOOKAHEAD_DAYS * 86400

ICONS = {'current': 'media-record-symbolic', 'soon': 'alarm-symbolic', 'upcoming': 'x-office-calendar-symbolic'}


def safe_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 or ord(c) == 127 or c == '\\' for c in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme.lower() == 'https' and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False



def account_url(value, email):
    """Select the originating calendar account on Google meeting/event links."""
    if not safe_url(value) or not isinstance(email, str) or '@' not in email:
        return value
    url = urlsplit(value)
    host = url.hostname.lower()
    if host not in ('meet.google.com', 'calendar.google.com', 'www.google.com'):
        return value
    if host == 'www.google.com' and not url.path.startswith('/calendar/'):
        return value
    query = [(key, val) for key, val in parse_qsl(url.query, keep_blank_values=True) if key != 'authuser']
    query.append(('authuser', email))
    # Browser-specific numeric account paths must not override the email hint.
    path = re.sub(r'/calendar/u/\d+/', '/calendar/', url.path) if host != 'meet.google.com' else url.path
    return urlunsplit((url.scheme, url.netloc, path, urlencode(query), url.fragment))


def timestamp(value):
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Timed calendar events must include a timezone')
    return date.timestamp()


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: float
    end: float
    calendar_name: str = ''
    calendar_id: str = ''
    meeting_url: str = ''
    event_url: str = ''
    native_id: str = ''
    uid: str = ''
    recurrence_id: str = ''
    can_respond: bool = False
    response_reason: str = 'Respond using the calendar event'
    response_status: str = ''

    def response_payload(self, response):
        if response not in ('accepted', 'declined'):
            raise ValueError('Invalid invitation response')
        return {'calendarId': self.calendar_id, 'eventUid': self.uid,
                'recurrenceId': self.recurrence_id, 'start': self.start * 1000, 'response': response}


def normalize_events(items):
    events = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        if item.get('status') == 'cancelled' or any(a.get('self') and a.get('responseStatus') == 'declined'
                                                   for a in item.get('attendees', [])):
            continue
        try:
            start_text = item['start']['dateTime']
            start, end = timestamp(start_text), timestamp(item['end']['dateTime'])
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                continue
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        call = item.get('meetingUrl') or item.get('hangoutLink') or next((p.get('uri', '') for p in
            item.get('conferenceData', {}).get('entryPoints', []) if p.get('entryPointType') == 'video'), '')
        events.append(Event(
            id=f"{item['id']}:{start_text}", title=item.get('summary') or 'Untitled meeting', start=start, end=end,
            calendar_name=item.get('calendarName', ''), calendar_id=item.get('calendarId', ''),
            meeting_url=account_url(call, item.get('sourceAccountEmail', '')),
            event_url=account_url(item.get('htmlLink', ''), item.get('sourceAccountEmail', '')), native_id=item.get('nativeEventId', ''),
            uid=item.get('eventUid', ''), recurrence_id=item.get('recurrenceId', ''),
            can_respond=item.get('canRespond') is True, response_reason=item.get('responseReason') or 'Respond using the calendar event',
            response_status=item.get('responseStatus', '')))
    return sorted(events, key=lambda e: e.start)


def visible_events(events, now):
    return [event for event in events if event.end > now and event.start < now + LOOKAHEAD_SECONDS]


def meeting_status(event, now, minutes):
    if event.start <= now:
        return 'current'
    return 'soon' if event.start - now <= minutes * 60 else 'upcoming'


def due_reminders(events, now, minutes, seen):
    return [event for event in events if 0 < event.start - now <= minutes * 60 and event.id not in seen]


def format_duration(seconds):
    """Show the two largest adjacent units, rounding positive durations up to a minute."""
    minutes = max(0, math.ceil(seconds / 60))
    for size, suffix, next_size, next_suffix in [(10080, 'w', 1440, 'd'),
            (1440, 'd', 60, 'h'), (60, 'h', 1, 'm')]:
        if minutes >= size:
            whole, remainder = divmod(minutes, size)
            smaller = remainder // next_size
            return f'{whole}{suffix}' + (f' {smaller}{next_suffix}' if smaller else '')
    return f'{minutes}m'


def panel_state(events, now, minutes, error='', running=True):
    events = visible_events(events, now)
    if events:
        event = events[0]
        status = meeting_status(event, now, minutes)
        suffix = 'now' if status == 'current' else f'in {format_duration(event.start - now)}'
        title = event.title if len(event.title) <= 32 else event.title[:31] + '…'
        return {'label': f'{title} · {suffix}', 'icon': ICONS[status], 'visible': running}
    return {'label': 'Calendar · attention' if error else 'No upcoming meetings',
            'icon': 'dialog-warning-symbolic' if error else ICONS['upcoming'], 'visible': running}
