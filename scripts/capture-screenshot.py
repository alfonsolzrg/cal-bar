#!/usr/bin/python3
"""Capture the private documentation desktop via GNOME's screenshot-app interface."""
import os
from pathlib import Path
import sys
from gi.repository import Gio, GLib

if os.environ.get('CAL_BAR_DOCS_ISOLATED') != '1':
    raise SystemExit('Run scripts/capture-docs.sh; this tool is only for its private compositor.')

filename = str(Path(sys.argv[1]).resolve())
loop = GLib.MainLoop()
result_code = 1


def complete(connection, result):
    global result_code
    try:
        success, used = connection.call_finish(result).unpack()
        if not success:
            raise RuntimeError('Screenshot capture failed')
        print(used)
        result_code = 0
    except Exception as error:
        print(error, file=sys.stderr)
    loop.quit()


def acquired(connection, _name):
    connection.call('org.gnome.Shell.Screenshot', '/org/gnome/Shell/Screenshot',
        'org.gnome.Shell.Screenshot', 'ScreenshotArea',
        GLib.Variant('(iiiibs)', (0, 0, 1280, 800, False, filename)),
        GLib.VariantType.new('(bs)'), Gio.DBusCallFlags.NONE, 10000, None, complete)


# This is GNOME's documented screenshot-app role, on the isolated session bus only.
owner = Gio.bus_own_name(Gio.BusType.SESSION, 'org.gnome.Screenshot', Gio.BusNameOwnerFlags.NONE,
                         None, acquired, None)
GLib.timeout_add_seconds(12, lambda: (loop.quit(), False)[1])
loop.run()
Gio.bus_unown_name(owner)
sys.exit(result_code)
