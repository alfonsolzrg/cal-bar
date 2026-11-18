// Stable launcher: preferences are implemented in the restartable companion.
import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class CalBarPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const launch = () => {
            const path = GLib.build_filenamev([GLib.get_user_data_dir(), 'cal-bar', 'cal_bar_ctl.py']);
            Gio.Subprocess.new(['/usr/bin/python3', path, 'preferences'],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
        };
        const page = new Adw.PreferencesPage({title: 'Cal Bar'});
        const group = new Adw.PreferencesGroup({title: 'Cal Bar settings', description:
            'Settings now open in the Cal Bar app. App updates no longer need a desktop logout.'});
        const row = new Adw.ActionRow({title: 'Open Cal Bar settings'});
        const button = new Gtk.Button({label: 'Open', valign: Gtk.Align.CENTER});
        button.connect('clicked', launch);
        row.add_suffix(button);
        group.add(row);
        page.add(group);
        window.add(page);
        launch();
    }
}
