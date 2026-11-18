// Stable platform bridge. Calendar logic and every Cal Bar window live in the companion.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const BUS = 'io.github.calbar.Companion';
const PATH = '/io/github/calbar/Companion/Control';
const IFACE = `${BUS}.Control`;
const TITLES = {agenda: 'Cal Bar — Meetings', reminder: 'Cal Bar — Reminder', dimmer: 'Cal Bar — Dimmer'};

export default class CalBarBridge extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._cancellable = new Gio.Cancellable();
        this._windowSignals = new Map();
        this._readyWindows = new Set();
        this._unmanaging = new WeakSet();
        this._windowTimers = new Set();
        this._lastStart = -30000000;
        this._button = new PanelMenu.Button(0.0, 'Cal Bar', true);
        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._icon = new St.Icon({icon_name: 'x-office-calendar-symbolic', style_class: 'system-status-icon'});
        this._label = new St.Label({text: 'Cal Bar', y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._icon);
        box.add_child(this._label);
        this._button.add_child(box);
        Main.panel.addToStatusArea(this.uuid, this._button);
        this._button.connect('button-press-event', () => { this._activate(); return Clutter.EVENT_STOP; });
        this._button.connect('key-press-event', (_actor, event) => {
            if ([Clutter.KEY_Return, Clutter.KEY_space, Clutter.KEY_KP_Enter].includes(event.get_key_symbol())) {
                this._activate();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._settingsSignal = this._settings.connect('changed::app-enabled', () => {
            this._button.visible = this._settings.get_boolean('app-enabled') && !Main.sessionMode.isLocked;
            if (this._button.visible) this._ensureRunning();
        });
        this._button.visible = this._settings.get_boolean('app-enabled') && !Main.sessionMode.isLocked;
        this._sessionSignal = Main.sessionMode.connect('updated', () => this._sendContext());
        this._focusSignal = global.display.connect('notify::focus-window', () => this._sendContext());
        this._createdSignal = global.display.connect('window-created', (_display, window) => {
            const signal = window.connect('notify::title', () => this._arrange(window));
            const unmanaging = window.connect('unmanaging', () => this._unmanaging.add(window));
            const unmanaged = window.connect('unmanaged', () => {
                this._windowSignals.delete(window);
                this._readyWindows.delete(window);
            });
            const resized = window.connect('size-changed', () => this._arrange(window));
            const shown = window.connect('shown', () => {
                const source = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
                    this._windowTimers.delete(source);
                    if (this._windowSignals.has(window)) {
                        this._readyWindows.add(window);
                        this._arrange(window);
                    }
                    return GLib.SOURCE_REMOVE;
                });
                this._windowTimers.add(source);
            });
            this._windowSignals.set(window, [signal, unmanaged, unmanaging, resized, shown]);
            this._arrange(window);
        });
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
            BUS, PATH, IFACE, this._cancellable, (_source, result) => {
                try {
                    const proxy = Gio.DBusProxy.new_for_bus_finish(result);
                    if (this._cancellable.is_cancelled()) return;
                    this._proxy = proxy;
                    this._ownerSignal = proxy.connect('notify::g-name-owner', () => this._ownerChanged());
                    this._signal = proxy.connect('g-signal', (_proxy, _sender, name, parameters) => {
                        if (name === 'StateChanged') this._state(parameters.deepUnpack()[0]);
                        if (name === 'Present') this._present(parameters.deepUnpack()[0]);
                    });
                    this._ownerChanged();
                } catch (error) {
                    if (!this._cancellable.is_cancelled()) console.warn(`Cal Bar bridge: ${error.message}`);
                }
            });
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            if (this._proxy?.g_name_owner) this._sendContext();
            else this._ensureRunning();
            return GLib.SOURCE_CONTINUE;
        });
        this._ensureRunning();
    }

    _call(method, parameters = null, callback = null) {
        if (!this._proxy?.g_name_owner) return;
        this._proxy.call(method, parameters, Gio.DBusCallFlags.NO_AUTO_START, 3000, this._cancellable, (proxy, result) => {
            try {
                const value = proxy.call_finish(result);
                if (!this._cancellable.is_cancelled()) callback?.(value);
            } catch (error) {
                const warmingUp = !this._ready && error.matches?.(Gio.DBusError, Gio.DBusError.UNKNOWN_METHOD);
                if (!this._cancellable.is_cancelled() && !warmingUp) console.warn(`Cal Bar bridge: ${error.message}`);
            }
        });
    }

    _ownerChanged() {
        this._ready = false;
        if (!this._proxy.g_name_owner) {
            this._label.text = 'Cal Bar · restarting';
            return;
        }
        this._sendContext();
        this._call('GetState', null, value => this._state(value.deepUnpack()[0]));
    }

    _state(json) {
        try {
            const state = JSON.parse(json);
            this._ready = true;
            this._label.text = String(state.label || 'Cal Bar');
            this._icon.icon_name = String(state.icon || 'x-office-calendar-symbolic');
            this._button.visible = state.visible !== false && this._settings.get_boolean('app-enabled') && !Main.sessionMode.isLocked;
        } catch { /* A companion update must not break the desktop panel. */ }
    }

    _ensureRunning() {
        if (!this._settings.get_boolean('app-enabled') || GLib.get_monotonic_time() - this._lastStart < 30000000) return;
        this._lastStart = GLib.get_monotonic_time();
        const launcher = GLib.build_filenamev([GLib.get_user_data_dir(), 'cal-bar', 'cal_bar_ctl.py']);
        try {
            Gio.Subprocess.new(['/usr/bin/python3', launcher, 'background'],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
        } catch (error) { console.warn(`Cal Bar launcher: ${error.message}`); }
    }

    _activate() {
        if (!this._proxy?.g_name_owner) {
            this._ensureRunning();
            return;
        }
        this._sendContext();
        this._call('Activate', new GLib.Variant('(s)', ['toggle-agenda']));
    }

    _sendContext() {
        this._button.visible = this._settings.get_boolean('app-enabled') && !Main.sessionMode.isLocked;
        const focus = global.display.focus_window;
        const focusedRole = this._isOurs(focus)
            ? Object.keys(TITLES).find(role => TITLES[role] === focus.get_title()) || 'preferences' : null;
        const monitor = Main.layoutManager.monitors[global.display.get_current_monitor()] || Main.layoutManager.primaryMonitor;
        this._call('SetSessionState', new GLib.Variant('(s)', [JSON.stringify({
            locked: Main.sessionMode.isLocked,
            monitor: monitor ? {x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height} : null,
            bridgeVersion: 1, focusedRole,
        })]));
    }

    _isOurs(window) {
        return Boolean(window) && (window.get_gtk_application_id() === BUS || window.get_wm_class() === BUS);
    }

    _arrange(window) {
        if (this._unmanaging.has(window) || !this._isOurs(window) || Main.sessionMode.isLocked ||
            (this._windowSignals.has(window) && !this._readyWindows.has(window)) ||
            !window.get_compositor_private()) return;
        const title = window.get_title();
        if (this._pendingRole && GLib.get_monotonic_time() < this._pendingUntil &&
            (title === TITLES[this._pendingRole] || (this._pendingRole === 'preferences' && title === 'Cal Bar Settings'))) {
            this._pendingRole = null;
            Main.activateWindow(window);
            this._sendContext();
        }
        if (!Object.values(TITLES).includes(title)) return;
        window.make_above();
        if (title === TITLES.agenda) {
            const monitor = Main.layoutManager.findMonitorForActor(this._button) || Main.layoutManager.primaryMonitor;
            const rect = window.get_frame_rect();
            const [x] = this._button.get_transformed_position();
            window.move_frame(false, Math.max(monitor.x, Math.min(x + this._button.width - rect.width,
                monitor.x + monitor.width - rect.width)), monitor.y + Main.panel.height + 8);
        }
    }

    _present(role) {
        if (!Main.sessionMode.isLocked) Main.overview.hide();
        this._pendingRole = role;
        this._pendingUntil = GLib.get_monotonic_time() + 3000000;
        this._sendContext();
        const windows = global.get_window_actors().map(actor => actor.meta_window).filter(window => this._isOurs(window));
        for (const window of windows) this._arrange(window);
        const target = windows.find(window => window.get_title() === TITLES[role] ||
            (role === 'preferences' && window.get_title() === 'Cal Bar Settings'));
        if (target && (!this._windowSignals.has(target) || this._readyWindows.has(target)) &&
            target.get_compositor_private() && !Main.sessionMode.isLocked) Main.activateWindow(target);
    }

    disable() {
        this._cancellable.cancel();
        GLib.Source.remove(this._timer);
        this._settings.disconnect(this._settingsSignal);
        Main.sessionMode.disconnect(this._sessionSignal);
        global.display.disconnect(this._createdSignal);
        global.display.disconnect(this._focusSignal);
        for (const [window, signals] of this._windowSignals) {
            for (const signal of signals) window.disconnect(signal);
        }
        this._windowSignals.clear();
        this._readyWindows.clear();
        for (const source of this._windowTimers) GLib.Source.remove(source);
        this._windowTimers.clear();
        if (this._proxy) {
            this._proxy.disconnect(this._ownerSignal);
            this._proxy.disconnect(this._signal);
        }
        // Preserve the user's enabled choice across Shell shutdown and desktop login.
        const launcher = GLib.build_filenamev([GLib.get_user_data_dir(), 'cal-bar', 'cal_bar_ctl.py']);
        Gio.Subprocess.new(['/usr/bin/python3', launcher, 'stop'],
            Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
        this._button.destroy();
        this._proxy = null;
    }
}
