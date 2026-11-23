#!/usr/bin/python3
"""Read GNOME Online Accounts calendars through Evolution Data Server."""
import argparse
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
import sys

from app_logic import LOOKAHEAD_DAYS

DEPENDENCIES = ('Install the GNOME calendar integration: sudo apt install python3-gi '
                'gir1.2-ecal-2.0 gir1.2-edataserver-1.2 gir1.2-goa-1.0')


def load_libraries():
    try:
        import gi
        for name, version in [('Goa', '1.0'), ('EDataServer', '1.2'),
                              ('ECal', '2.0')]:
            gi.require_version(name, version)
        from gi.repository import Goa, EDataServer, ECal
        # ECal already loaded the ICalGLib it links against (3.0 on Ubuntu 24.04, 4.0 with libical 4).
        from gi.repository import ICalGLib
        return Goa, EDataServer, ECal, ICalGLib
    except (ImportError, ValueError) as error:
        raise RuntimeError(DEPENDENCIES) from error


def discover_calendars(registry, goa, eds):
    accounts = {}
    for obj in goa.get_accounts():
        account = obj.get_account()
        if account and obj.get_calendar() and not account.props.calendar_disabled:
            accounts[account.props.id] = account
    calendars = []
    for source in registry.list_sources(eds.SOURCE_EXTENSION_CALENDAR):
        parent = registry.find_extension(source, eds.SOURCE_EXTENSION_GOA)
        if not parent or not registry.check_enabled(source):
            continue
        account_id = parent.get_extension(eds.SOURCE_EXTENSION_GOA).get_account_id()
        account = accounts.get(account_id)
        if not account:
            continue
        calendars.append((source, {
            'id': source.get_uid(), 'name': source.get_display_name(),
            'account': account.props.presentation_identity,
            'needsAttention': account.props.attention_needed,
        }))
    return sorted(calendars, key=lambda entry: (entry[1]['account'], entry[1]['name']))


def selected_calendars(calendars, selected):
    # None means all; an empty selection means none.
    return calendars if selected is None else [entry for entry in calendars if entry[1]['id'] in selected]


def event_links(component, ical):
    conference = ''
    prop = component.get_first_property(ical.PropertyKind.X_PROPERTY)
    while prop:
        if (prop.get_x_name() or '').upper() in ('X-GOOGLE-CONFERENCE', 'X-MICROSOFT-SKYPETEAMSMEETINGURL'):
            value = prop.get_x()
            if value and value.lower().startswith('https://'):
                conference = value
                break
        prop = component.get_next_property(ical.PropertyKind.X_PROPERTY)
    prop = component.get_first_property(ical.PropertyKind.URL_PROPERTY)
    url = prop.get_url() if prop else ''
    def call_link(value):
        try:
            host = (urlsplit(value).hostname or '').lower()
            return host in ('meet.google.com', 'teams.microsoft.com', 'teams.live.com') or \
                host == 'zoom.us' or host.endswith('.zoom.us') or \
                host == 'zoom.com' or host.endswith('.zoom.com')
        except ValueError:
            return False
    if url and call_link(url):
        conference = conference or url
        url = ''
    for text in (component.get_location(), component.get_description()):
        for match in re.finditer(r'https://[^\s<>"\)]+', text or ''):
            value = match.group(0).rstrip('.,;')
            if not conference and call_link(value):
                conference = value
    # Never treat the call URL itself as a link to the calendar event.
    return conference, url if url != conference else ''


def own_attendee(component, own_emails, ical):
    prop = component.get_first_property(ical.PropertyKind.ATTENDEE_PROPERTY)
    while prop:
        email = (prop.get_attendee() or '').lower().removeprefix('mailto:')
        if email in own_emails:
            return prop
        prop = component.get_next_property(ical.PropertyKind.ATTENDEE_PROPERTY)
    return None


def invitation_state(component, own_emails, ical):
    attendee = own_attendee(component, own_emails, ical)
    organizer = component.get_first_property(ical.PropertyKind.ORGANIZER_PROPERTY)
    if not attendee or not organizer:
        return False, 'Not an invitation for this account', ''
    if (organizer.get_organizer() or '').lower().removeprefix('mailto:') in own_emails:
        return False, 'You organize this event', ''
    return True, '', partstat_name(attendee, ical)


def partstat_name(prop, ical):
    param = prop.get_first_parameter(ical.ParameterKind.PARTSTAT_PARAMETER)
    value = param.get_partstat() if param else None
    # libical 3 enums carry value_nick; libical 4 (PyGObject 3.52+) enums are Python enums.
    return (getattr(value, 'value_nick', None) or value.name.lower()) if value is not None else 'needsaction'


def instance_to_event(component, start, end, calendar, own_emails, ical, default_timezone=None):
    if start.is_date() or end.is_date():
        return None
    status = component.get_first_property(ical.PropertyKind.STATUS_PROPERTY)
    if status and status.get_status() == ical.PropertyStatus.CANCELLED:
        return None
    organizer = component.get_first_property(ical.PropertyKind.ORGANIZER_PROPERTY)
    organizer = (organizer.get_organizer() or '').lower().removeprefix('mailto:') if organizer else ''
    attendees = []
    prop = component.get_first_property(ical.PropertyKind.ATTENDEE_PROPERTY)
    while prop:
        email = (prop.get_attendee() or '').lower().removeprefix('mailto:')
        status = partstat_name(prop, ical)
        if email in own_emails and status == 'declined':
            return None
        cn = prop.get_first_parameter(ical.ParameterKind.CN_PARAMETER)
        # Same shape as Google's REST attendees, so both sources normalize alike.
        attendees.append({'email': email, 'displayName': cn.get_cn() if cn else '', 'responseStatus': status,
                          'organizer': email == organizer, 'self': email in own_emails})
        prop = component.get_next_property(ical.PropertyKind.ATTENDEE_PROPERTY)
    join_url, event_url = event_links(component, ical)
    can_respond, reason, response = invitation_state(component, own_emails, ical)
    rid_property = component.get_first_property(ical.PropertyKind.RECURRENCEID_PROPERTY)
    recurring = bool(rid_property or component.get_first_property(ical.PropertyKind.RRULE_PROPERTY) or
                     component.get_first_property(ical.PropertyKind.RDATE_PROPERTY))
    rid = rid_property.get_value_as_string() if rid_property else (start.as_ical_string() if recurring else '')
    def timestamp(value):
        zone = value.get_timezone() or default_timezone or ical.Timezone.get_utc_timezone()
        return datetime.fromtimestamp(value.as_timet_with_zone(zone), timezone.utc).isoformat()
    return {
        'id': f"{calendar['id']}:{component.get_uid()}",
        'summary': component.get_summary() or 'Untitled meeting',
        'start': {'dateTime': timestamp(start)}, 'end': {'dateTime': timestamp(end)},
        'meetingUrl': join_url, 'htmlLink': event_url,
        'eventUid': component.get_uid(), 'recurrenceId': rid,
        'nativeEventId': f"{calendar['id']}:{component.get_uid()}" + (f':{rid}' if rid else ''),
        'canRespond': can_respond, 'responseReason': reason, 'responseStatus': response,
        'calendarName': calendar['name'], 'calendarId': calendar['id'], 'attendees': attendees,
    }


def read_events(calendars, ecal, eds, ical, now=None):
    now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    items, warnings = [], []
    for source, info in calendars:
        if info['needsAttention']:
            warnings.append(f"Reconnect {info['account']} in GNOME Online Accounts.")
            continue
        try:
            client = ecal.Client.connect_sync(source, ecal.ClientSourceType.EVENTS, 5, None)
            client.set_default_timezone(ical.Timezone.get_utc_timezone())
            # Floating events use the system timezone, as they do in GNOME Calendar.
            from pathlib import Path
            zone = None
            try:
                zone = Path('/etc/timezone').read_text().strip()
            except OSError:
                try:
                    zone = str(Path('/etc/localtime').resolve()).split('/zoneinfo/', 1)[1]
                except (OSError, IndexError):
                    pass
            if zone:
                tz = ical.Timezone.get_builtin_timezone(zone)
                if tz:
                    client.set_default_timezone(tz)
            emails = calendar_emails(client, info['account'])
            def collect(component, start, end, *unused):
                item = instance_to_event(component, start, end, info, emails, ical, client.get_default_timezone())
                if item:
                    if client.is_readonly() or not client.check_save_schedules():
                        item['canRespond'] = False
                        item['responseReason'] = 'Respond in Calendar (this calendar does not support direct replies)'
                    items.append(item)
                return True
            # Surface backend/query errors before ECal's void recurrence API.
            begin = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            finish = datetime.fromtimestamp(now + LOOKAHEAD_DAYS * 86400, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            ok, objects = client.get_object_list_sync(
                f'(occur-in-time-range? (make-time "{begin}") (make-time "{finish}"))', None)
            if not ok:
                raise RuntimeError('Calendar query failed.')
            # Fetch each UID with its full exception set: range queries can omit
            # declined exceptions while returning their recurring master.
            for uid in {comp.get_uid() for comp in objects if comp.get_uid()}:
                client.generate_instances_for_uid_sync(uid, now, now + LOOKAHEAD_DAYS * 86400, None, collect, None)
        except Exception:
            warnings.append(f"Could not read {info['name']} ({info['account']}). Check calendar access in GNOME Online Accounts.")
    unique = {(item['id'], item['start']['dateTime']): item for item in items}
    return list(unique.values()), warnings


def query(command, selected=None):
    goa, eds, ecal, ical = load_libraries()
    registry = eds.SourceRegistry.new_sync(None)
    accounts = goa.Client.new_sync(None)
    calendars = discover_calendars(registry, accounts, eds)
    result = {'calendars': [info for _, info in calendars]}
    if command == 'events':
        chosen = selected_calendars(calendars, selected)
        result['items'], result['warnings'] = read_events(chosen, ecal, eds, ical)
        if not calendars:
            result['warnings'].append('Connect an account in GNOME Online Accounts and enable Calendars.')
        elif not chosen:
            result['warnings'].append('No calendars selected. Choose calendars in Cal Bar preferences.')
    return result


def calendar_emails(client, account):
    emails = {account.lower()}
    try:
        ok, address = client.get_backend_property_sync('cal-email-address', None)
        if ok and address:
            emails.add(address.lower().removeprefix('mailto:'))
    except Exception:
        pass
    return emails


def set_response(component, emails, response, ical):
    if response not in ('accepted', 'declined'):
        raise RuntimeError('Invalid invitation response.')
    eligible, reason, _ = invitation_state(component, emails, ical)
    if not eligible:
        raise RuntimeError(reason)
    attendee = own_attendee(component, emails, ical)
    value = ical.ParameterPartstat.ACCEPTED if response == 'accepted' else ical.ParameterPartstat.DECLINED
    attendee.set_parameter(ical.Parameter.new_partstat(value))
    attendee.set_parameter(ical.Parameter.new_rsvp(ical.ParameterRsvp.FALSE))
    return component


def respond(payload):
    goa, eds, ecal, ical = load_libraries()
    registry = eds.SourceRegistry.new_sync(None)
    calendars = discover_calendars(registry, goa.Client.new_sync(None), eds)
    entry = next((entry for entry in calendars if entry[1]['id'] == payload.get('calendarId')), None)
    if not entry or entry[1]['needsAttention']:
        raise RuntimeError('This account is unavailable. Reconnect it in GNOME Online Accounts.')
    source, info = entry
    client = ecal.Client.connect_sync(source, ecal.ClientSourceType.EVENTS, 5, None)
    if client.is_readonly() or not client.check_save_schedules():
        raise RuntimeError('This calendar cannot send replies directly. Respond in Calendar instead.')
    uid, rid = payload.get('eventUid'), payload.get('recurrenceId') or None
    if not isinstance(uid, str) or not uid:
        raise RuntimeError('The event no longer has a valid identifier. Refresh the calendar.')
    # Read the latest event; never send the cached object back over newer organizer edits.
    if rid:
        occurrence = int(payload['start'] / 1000)
        matches = []
        def match(comp, start, end, *unused):
            zone = start.get_timezone() or client.get_default_timezone()
            if comp.get_uid() == uid and start.as_timet_with_zone(zone) == occurrence:
                instance = comp.clone()
                # ECal passes instance times separately; the component can still
                # carry the recurring master's original DTSTART and DTEND.
                instance.set_dtstart(start)
                instance.set_dtend(end)
                if not instance.get_first_property(ical.PropertyKind.RECURRENCEID_PROPERTY):
                    value = datetime.fromtimestamp(occurrence, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    instance.set_recurrenceid(ical.Time.new_from_string(value))
                matches.append(instance)
            return True
        client.generate_instances_for_uid_sync(uid, occurrence, occurrence + 1, None, match, None)
        if len(matches) != 1:
            raise RuntimeError('This occurrence changed. Refresh the calendar before responding.')
        component = matches[0]
        ok = True
        mod = ecal.ObjModType.THIS
        # Store a single-occurrence exception, never replace the recurring series.
        for kind in (ical.PropertyKind.RRULE_PROPERTY, ical.PropertyKind.RDATE_PROPERTY, ical.PropertyKind.EXDATE_PROPERTY):
            prop = component.get_first_property(kind)
            while prop:
                component.remove_property(prop)
                prop = component.get_first_property(kind)
    else:
        ok, component = client.get_object_sync(uid, None, None)
        if component.get_first_property(ical.PropertyKind.RRULE_PROPERTY) or component.get_first_property(ical.PropertyKind.RDATE_PROPERTY):
            raise RuntimeError('The event is now recurring. Refresh before responding to an occurrence.')
        mod = ecal.ObjModType.ALL
    if not ok:
        raise RuntimeError('This event is unavailable. Refresh the calendar.')
    set_response(component, calendar_emails(client, info['account']), payload.get('response'), ical)
    # CalDAV/server scheduling sends the attendee reply. Do not also email the organizer.
    if not client.modify_object_sync(component, mod, ecal.OperationFlags.NONE, None):
        raise RuntimeError('The calendar did not save your response. Try again in Calendar.')
    return {'ok': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['calendars', 'events', 'respond'])
    parser.add_argument('--sources', help='JSON list of selected EDS source IDs; omit for all calendars')
    args = parser.parse_args()
    try:
        selected = json.loads(args.sources) if args.sources is not None else None
        result = respond(json.load(sys.stdin)) if args.command == 'respond' else query(args.command, selected)
    except RuntimeError as error:
        result = {'error': str(error)}
    except Exception:
        result = {'error': ('Could not save your invitation response. Check account access and try in Calendar.'
                            if args.command == 'respond' else
                            'Cannot read GNOME calendars. Check that GNOME Online Accounts and Evolution Data Server are running.')}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
