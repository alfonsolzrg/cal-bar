import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.environ.get('CAL_BAR_EDS_TEST_ISOLATED') != '1':
    raise SystemExit('Run tests/run-eds-integration.sh to isolate this synthetic calendar test.')
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
import gnome_calendar as g
Goa,EDS,ECal,I=g.load_libraries()
registry=EDS.SourceRegistry.new_sync(None)
source=EDS.Source.new_with_uid('cal-bar-rsvp-smoke',None)
source.set_display_name('Cal Bar isolated RSVP test')
source.get_extension(EDS.SOURCE_EXTENSION_CALENDAR).set_backend_name('local')
registry.commit_source_sync(source,None)
source=registry.ref_source('cal-bar-rsvp-smoke')
client=ECal.Client.connect_sync(source,ECal.ClientSourceType.EVENTS,5,None)
comp=I.Component.new_from_string('BEGIN:VEVENT\r\nUID:recurrence\r\nDTSTART:20261118T120000Z\r\nDTEND:20261118T130000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Synthetic RSVP test\r\nORGANIZER:mailto:organizer@example.invalid\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:me@example.invalid\r\nEND:VEVENT\r\n')
client.create_object_sync(comp,ECal.OperationFlags.NONE,None)
info={'id':source.get_uid(),'name':'Smoke','account':'me@example.invalid','needsAttention':False}
now=int(datetime(2026,11,18,tzinfo=timezone.utc).timestamp())
items,warnings=g.read_events([(source,info)],ECal,EDS,I,now)
assert len(items)==3 and not warnings
items.sort(key=lambda e:e['start']['dateTime'])
class Proxy:
 def __getattr__(self,name):return getattr(client,name)
 def check_save_schedules(self):return True
 def is_readonly(self):return False
proxy=Proxy()
fake_ecal=SimpleNamespace(Client=SimpleNamespace(connect_sync=lambda *args:proxy),ClientSourceType=ECal.ClientSourceType,ObjModType=ECal.ObjModType,OperationFlags=ECal.OperationFlags)
chosen=items[1]
payload={'calendarId':info['id'],'eventUid':chosen['eventUid'],'recurrenceId':chosen['recurrenceId'],
 'start':int(datetime.fromisoformat(chosen['start']['dateTime']).timestamp()*1000),'response':'declined'}
with patch.object(g,'load_libraries',return_value=(Goa,EDS,fake_ecal,I)), patch.object(g,'discover_calendars',return_value=[(source,info)]):
 assert g.respond(payload)=={'ok':True}
_,master=client.get_object_sync('recurrence',None,None)
assert 'PARTSTAT=NEEDS-ACTION' in master.as_ical_string(),master.as_ical_string()
items,warnings=g.read_events([(source,info)],ECal,EDS,I,now)
assert len(items)==2, (len(items), warnings)
assert all(e['start']['dateTime']!=chosen['start']['dateTime'] for e in items)
source.remove_sync(None)
print('Single-occurrence RSVP saved; recurring master and other occurrences preserved. No external account used.')
