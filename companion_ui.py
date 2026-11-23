"""All Cal Bar windows live in the restartable companion process."""
from datetime import datetime
import time
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk
from app_logic import LOOKAHEAD_DAYS, ICONS, meeting_status, safe_url, visible_events
from companion_runtime import run_helper
from reminder_melodies import MELODIES

TITLES = {'agenda': 'Cal Bar — Meetings', 'reminder': 'Cal Bar — Reminder', 'dimmer': 'Cal Bar — Dimmer'}


def button(label, callback, **kwargs):
    widget = Gtk.Button(label=label, **kwargs)
    widget.connect('clicked', lambda *_: callback())
    return widget


def spaced_box(spacing=12, margin=20, **kwargs):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kwargs)
    box.set_margin_top(margin)
    box.set_margin_bottom(margin)
    box.set_margin_start(margin)
    box.set_margin_end(margin)
    return box


def escape_closes(window, callback):
    keys = Gtk.EventControllerKey()
    def pressed(_controller, key, _code, _modifiers):
        if key == Gdk.KEY_Escape:
            callback()
            return True
        return False
    keys.connect('key-pressed', pressed)
    window.add_controller(keys)


def time_label(value):
    return datetime.fromtimestamp(value).strftime('%a %d %b · %H:%M')


class AgendaWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=TITLES['agenda'], decorated=False,
                         default_width=560, default_height=640)
        self.app = app
        self.had_focus = False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(box)
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label='Upcoming meetings', css_classes=['heading']))
        header.set_show_title_buttons(False)
        header.pack_start(button('Settings', app.show_preferences))
        menu = Gtk.MenuButton(icon_name='open-menu-symbolic')
        popover = Gtk.Popover()
        actions = spaced_box(spacing=6, margin=8)
        for label, callback in [('Refresh calendars', app.sync), ('Preview reminder', app.preview),
                                ('Restart Cal Bar', app.restart), ('Quit Cal Bar', app.quit_app)]:
            def activate(callback=callback):
                popover.popdown()
                callback()
            actions.append(button(label, activate))
        popover.set_child(actions)
        menu.set_popover(popover)
        header.pack_end(menu)
        box.append(header)
        self.message = Gtk.Label(wrap=True, xalign=0, max_width_chars=64, css_classes=['dim-label'])
        self.message.set_margin_start(20)
        self.message.set_margin_end(20)
        self.message.set_margin_top(12)
        box.append(self.message)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.list = spaced_box(spacing=10, margin=16)
        self.scroll.set_child(self.list)
        box.append(self.scroll)
        self.connect('close-request', lambda *_: (self.set_visible(False), True)[1])
        self.connect('notify::is-active', self._active_changed)
        escape_closes(self, lambda: self.set_visible(False))

    def _active_changed(self, *_):
        if self.is_active():
            self.had_focus = True
        elif self.had_focus:
            GLib.idle_add(self._hide_if_unfocused)

    def _hide_if_unfocused(self):
        if not self.is_active():
            self.set_visible(False)
        return GLib.SOURCE_REMOVE

    def render(self):
        expanded = set()
        child = self.list.get_first_child()
        while child:
            if isinstance(child, Gtk.Expander) and child.get_expanded():
                expanded.add(child.event_id)
            next_child = child.get_next_sibling()
            self.list.remove(child)
            child = next_child
        self.message.set_text(self.app.error or f'Next {LOOKAHEAD_DAYS} days · expand an event for links and invitation replies')
        events = visible_events(self.app.events, time.time())
        if not events:
            self.list.append(Gtk.Label(label='No timed meetings scheduled', margin_top=32, margin_bottom=32))
        for event in events:
            row = Gtk.Expander(expanded=event.id in expanded)
            row.event_id = event.id
            row.add_css_class('card')
            summary = Gtk.Box(spacing=12)
            status = meeting_status(event, time.time(), self.app.settings.get_int('reminder-minutes'))
            summary.append(Gtk.Image(icon_name=ICONS[status]))
            text = spaced_box(spacing=4, margin=8, hexpand=True)
            text.append(Gtk.Label(label=event.title, wrap=True, xalign=0, max_width_chars=48, css_classes=['heading']))
            text.append(Gtk.Label(label=f'{time_label(event.start)} – {datetime.fromtimestamp(event.end):%H:%M}',
                                  xalign=0, css_classes=['dim-label']))
            if event.calendar_name:
                text.append(Gtk.Label(label=event.calendar_name, xalign=0, css_classes=['dim-label']))
            summary.append(text)
            row.set_label_widget(summary)
            options = spaced_box(spacing=10, margin=16)
            join = button('Join meeting', lambda e=event: self.app.open_url(e.meeting_url))
            join.set_sensitive(safe_url(event.meeting_url))
            options.append(join)
            details = button('Open calendar event' if safe_url(event.event_url) else 'Open event in GNOME Calendar',
                             lambda e=event: self.app.open_event(e))
            details.set_sensitive(safe_url(event.event_url) or bool(event.native_id))
            options.append(details)
            if event.response_status:
                status = {'needsaction': 'Not answered', 'accepted': 'Accepted', 'tentative': 'Tentative',
                          'declined': 'Declined'}.get(event.response_status, event.response_status)
                options.append(Gtk.Label(label=f'Your response: {status}', xalign=0))
            replies = Gtk.Box(spacing=10, homogeneous=True)
            for label, response in [('Accept', 'accepted'), ('Reject', 'declined')]:
                action = button(label, lambda e=event, r=response: self.app.respond(e, r), hexpand=True)
                action.set_sensitive(event.can_respond and not self.app.response_busy and event.response_status != response)
                replies.append(action)
            options.append(replies)
            note = ('Reply for this occurrence only' if event.recurrence_id else 'Sends your response to the organizer') \
                if event.can_respond else event.response_reason
            options.append(Gtk.Label(label=note, wrap=True, xalign=0, max_width_chars=50, css_classes=['dim-label']))
            row.set_child(options)
            self.list.append(row)


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Cal Bar Settings', default_width=660, default_height=720)
        self.app, self.settings = app, app.settings
        self.rows = []
        self.job = None
        self.closed = False
        self.fingerprint = None
        self.updating = False
        page = Adw.PreferencesPage(title='Cal Bar', icon_name='x-office-calendar-symbolic')
        self.add(page)
        accounts = Adw.PreferencesGroup(title='Connected accounts', description=
            'Connect opens GNOME Online Accounts. Choose a provider, sign in, and enable Calendars.')
        page.add(accounts)
        account = Adw.ActionRow(title='Connect a calendar account', subtitle='GNOME manages accounts and sign-in.')
        account.add_suffix(button('Connect', lambda: app.launch(['gnome-control-center', 'online-accounts']), valign=Gtk.Align.CENTER))
        accounts.add(account)
        self.calendars = Adw.PreferencesGroup(title='Calendars', description=
            'Combine calendars from multiple accounts, or choose individual calendars.')
        page.add(self.calendars)
        self.all = Adw.SwitchRow(title='Use all connected calendars', subtitle='Automatically includes new calendars.')
        self.settings.bind('all-calendars', self.all, 'active', 0)
        self.calendars.add(self.all)
        self.status = Adw.ActionRow(title='Available calendars', subtitle='Looking for calendars…')
        self.refresh_button = button('Refresh', lambda: (app.sync(), self.refresh()), valign=Gtk.Align.CENTER)
        self.status.add_suffix(self.refresh_button)
        self.calendars.add(self.status)
        reminders = Adw.PreferencesGroup(title='Meeting reminders', description=
            'Full-screen reminder on the active monitor, dimming other monitors. Escape dismisses it.')
        page.add(reminders)
        enabled = Adw.SwitchRow(title='Full-screen reminders')
        self.settings.bind('reminders-enabled', enabled, 'active', 0)
        reminders.add(enabled)
        sound = Adw.SwitchRow(title='Reminder sound',
            subtitle='Play a melody once. Respects GNOME sound settings and mute.')
        self.settings.bind('reminder-sound-enabled', sound, 'active', 0)
        reminders.add(sound)
        keys = [item[0] for item in MELODIES]
        melody = Adw.ComboRow(title='Melody', model=Gtk.StringList.new([item[1] for item in MELODIES]))
        selected = self.settings.get_string('reminder-melody')
        melody.set_selected(keys.index(selected) if selected in keys else 0)
        melody.connect('notify::selected', lambda row, *_:
            self.settings.set_string('reminder-melody', keys[row.get_selected()]))
        reminders.add(melody)
        listen = Adw.ActionRow(title='Preview melody', subtitle='About 14–15 seconds. Stops when you close settings.')
        listen.add_suffix(button('Play', lambda: app.sound.play(self.settings.get_string('reminder-melody')), valign=Gtk.Align.CENTER))
        listen.add_suffix(button('Stop', app.sound.stop, valign=Gtk.Align.CENTER))
        reminders.add(listen)
        minutes = Adw.SpinRow(title='Minutes before the meeting', subtitle='0 disables advance reminders',
            adjustment=Gtk.Adjustment(lower=0, upper=60, step_increment=1, page_increment=5))
        self.settings.bind('reminder-minutes', minutes, 'value', 0)
        reminders.add(minutes)
        runtime = Adw.PreferencesGroup(title='Application', description='Most updates only need a Cal Bar restart.')
        page.add(runtime)
        restart = Adw.ActionRow(title='Restart Cal Bar', subtitle='Restart the background app without logging out.')
        restart.add_suffix(button('Restart', app.restart, valign=Gtk.Align.CENTER))
        runtime.add(restart)
        self.setting_ids = [self.settings.connect('changed::all-calendars', lambda *_: self.update_rows()),
                            self.settings.connect('changed::selected-calendars', lambda *_: self.update_rows())]
        self.timer = GLib.timeout_add_seconds(10, self.refresh)
        self.connect('close-request', self._closed)
        self.connect('notify::is-active', lambda *_: self.refresh() if self.is_active() else None)
        self.refresh()

    def _closed(self, *_):
        self.cleanup()
        self.app.preferences = None
        return False

    def cleanup(self):
        self.app.sound.stop()
        if self.closed:
            return
        self.closed = True
        if self.job:
            self.job.cancel()
        GLib.source_remove(self.timer)
        for signal in self.setting_ids:
            self.settings.disconnect(signal)

    def update_rows(self):
        self.updating = True
        selected = self.settings.get_strv('selected-calendars')
        for identifier, row, check in self.rows:
            row.set_sensitive(not self.all.get_active())
            check.set_active(self.all.get_active() or identifier in selected)
        self.updating = False

    def refresh(self):
        if self.closed or self.job:
            return GLib.SOURCE_CONTINUE
        self.refresh_button.set_sensitive(False)
        self.job = run_helper(self.settings, 'calendars', self._refreshed)
        return GLib.SOURCE_CONTINUE

    def _refreshed(self, data, error):
        self.job = None
        if self.closed:
            return
        self.refresh_button.set_sensitive(True)
        if error:
            self.status.set_subtitle(error)
            return
        calendars = data['calendars']
        if calendars != self.fingerprint:
            for _, row, _ in self.rows:
                self.calendars.remove(row)
            self.rows = []
            for calendar in calendars:
                row = Adw.ActionRow(title=calendar['name'], subtitle=calendar['account'] +
                    (' · Reconnect in Online Accounts' if calendar['needsAttention'] else ''))
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                check.connect('toggled', self._selected, calendar['id'])
                row.add_prefix(check)
                row.set_activatable_widget(check)
                self.calendars.add(row)
                self.rows.append((calendar['id'], row, check))
            self.update_rows()
            if self.fingerprint is not None:
                self.app.sync()
            self.fingerprint = calendars
        self.status.set_subtitle(f'{len(calendars)} calendars found. Turn off “Use all” to choose calendars.' if calendars
                                 else 'No calendars found. Click Connect and enable Calendars for your account.')

    def _selected(self, check, identifier):
        if self.updating or self.all.get_active():
            return
        selected = set(self.settings.get_strv('selected-calendars'))
        if check.get_active():
            selected.add(identifier)
        else:
            selected.discard(identifier)
        self.settings.set_strv('selected-calendars', sorted(selected))


def reminder_windows(app, event, active_geometry=None):
    monitors = Gdk.Display.get_default().get_monitors()
    candidates = [monitors.get_item(i) for i in range(monitors.get_n_items())]
    if not candidates:
        return []
    active = next((monitor for monitor in candidates if active_geometry and
        all(getattr(monitor.get_geometry(), key) == active_geometry.get(key) for key in ('x', 'y', 'width', 'height'))), candidates[0])
    windows = []
    for monitor in [m for m in candidates if m != active] + [active]:
        main = monitor == active
        window = Gtk.ApplicationWindow(application=app, title=TITLES['reminder' if main else 'dimmer'], decorated=False)
        window.add_css_class('cal-bar-reminder' if main else 'cal-bar-dimmer')
        window.connect('close-request', lambda *_: (app.dismiss_reminder(), True)[1])
        escape_closes(window, app.dismiss_reminder)
        if main:
            content = spaced_box(spacing=28, margin=64, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
            content.append(Gtk.Image(icon_name='alarm-symbolic', pixel_size=72))
            app.reminder_heading = Gtk.Label(label='YOUR MEETING IS COMING UP', css_classes=['reminder-eyebrow'])
            content.append(app.reminder_heading)
            content.append(Gtk.Label(label=event.title, wrap=True, justify=Gtk.Justification.CENTER,
                                     max_width_chars=38, css_classes=['reminder-title']))
            app.reminder_countdown = Gtk.Label(css_classes=['reminder-time'])
            content.append(app.reminder_countdown)
            actions = Gtk.Box(spacing=16, halign=Gtk.Align.CENTER)
            actions.append(button('Dismiss', app.dismiss_reminder))
            if safe_url(event.meeting_url):
                actions.append(button('Join meeting', lambda: (app.open_url(event.meeting_url), app.dismiss_reminder())))
            content.append(actions)
            window.set_child(content)
        else:
            area = Gtk.Box(hexpand=True, vexpand=True)
            click = Gtk.GestureClick()
            click.connect('released', lambda *_: app.dismiss_reminder())
            area.add_controller(click)
            window.set_child(area)
        window.present()
        # Wait for the initial surface to be managed before requesting fullscreen;
        # GNOME can otherwise receive fullscreen before assigning a stacking position.
        def fullscreen(window=window, monitor=monitor):
            if window.get_visible():
                window.fullscreen_on_monitor(monitor)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(100, fullscreen)
        windows.append(window)
    return windows
