from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from gi.repository import GLib
from app_logic import Event
from cal_bar import CalBarApplication
from companion_runtime import ReminderSound


class SoundPlayerTests(unittest.TestCase):
    def sound(self, enabled=True):
        desktop = Mock()
        desktop.get_boolean.return_value = enabled
        desktop.get_string.return_value = 'Yaru'
        return ReminderSound(desktop)

    def test_uses_one_themed_alarm_on_the_native_wayland_connection(self):
        sound = self.sound()
        with patch('companion_runtime.GLib.find_program_in_path', return_value='/usr/bin/canberra-gtk-play'), \
                patch('companion_runtime.GLib.getenv', return_value='wayland-0'), \
                patch('companion_runtime.Gio.SubprocessLauncher.new') as factory:
            sound.play()
        launcher = factory.return_value
        args, = launcher.spawnv.call_args.args
        self.assertIn('--id=alarm-clock-elapsed', args)
        self.assertIn('--loop=1', args)
        self.assertIn('--property=canberra.xdg-theme.name=Yaru', args)
        self.assertIn('--property=media.role=event', args)
        launcher.setenv.assert_called_once_with('GDK_BACKEND', 'wayland', True)
        sound.process.wait_async.assert_called_once()

    def test_desktop_sound_disable_prevents_playback(self):
        with patch('companion_runtime.GLib.find_program_in_path') as find:
            self.sound(enabled=False).play()
        find.assert_not_called()

    def test_missing_player_does_not_break_reminders(self):
        sound = self.sound()
        with patch('companion_runtime.GLib.find_program_in_path', return_value=None), self.assertLogs(level='WARNING'):
            sound.play()
        self.assertIsNone(sound.process)

    def test_audio_start_failure_is_nonfatal(self):
        sound = self.sound()
        with patch('companion_runtime.GLib.find_program_in_path', return_value='/usr/bin/canberra-gtk-play'), \
                patch('companion_runtime.Gio.SubprocessLauncher.new') as factory, self.assertLogs(level='WARNING'):
            factory.return_value.spawnv.side_effect = GLib.Error('Audio backend unavailable')
            sound.play()
        self.assertIsNone(sound.process)

    def test_stop_cancels_the_clip_without_clearing_a_later_sound(self):
        sound = self.sound()
        previous, current = Mock(), Mock()
        sound.process = previous
        sound.stop()
        previous.force_exit.assert_called_once()
        self.assertIsNone(sound.process)
        sound.process = current
        sound._finished(previous, Mock())
        self.assertIs(sound.process, current)
        current.get_successful.return_value = True
        sound._finished(current, Mock())
        self.assertIsNone(sound.process)


class ReminderSoundDispatchTests(unittest.TestCase):
    event = Event('invite', 'Design review', 1000, 2800, uid='invite')

    def app(self, enabled=True, locked=False):
        settings = Mock()
        settings.get_boolean.return_value = enabled
        return SimpleNamespace(locked=locked, notify=Mock(), dismiss_reminder=Mock(), agenda=None,
            geometry=None, reminder_event=None, reminders=[], seen=Mock(), tick=Mock(),
            request_presentation=Mock(), settings=settings, sound=Mock())

    def test_one_sound_for_two_monitor_windows(self):
        app = self.app()
        with patch('cal_bar.reminder_windows', return_value=[Mock(), Mock()]):
            CalBarApplication.show_reminder(app, self.event)
        app.sound.play.assert_called_once()
        app.seen.mark.assert_called_once_with(self.event)
        self.assertEqual(len(app.reminders), 2)

    def test_preview_plays_without_marking_a_real_event_seen(self):
        app = self.app()
        with patch('cal_bar.reminder_windows', return_value=[Mock()]):
            CalBarApplication.show_reminder(app, self.event, preview=True)
        app.sound.play.assert_called_once()
        app.seen.mark.assert_not_called()

    def test_sound_switch_leaves_the_visual_reminder_working(self):
        app = self.app(enabled=False)
        with patch('cal_bar.reminder_windows', return_value=[Mock()]):
            CalBarApplication.show_reminder(app, self.event)
        app.sound.play.assert_not_called()
        app.request_presentation.assert_called_once_with('reminder')
        self.assertEqual(len(app.reminders), 1)

    def test_locked_or_failed_reminders_do_not_make_sound(self):
        for locked, windows in [(True, [Mock()]), (False, [])]:
            with self.subTest(locked=locked):
                app = self.app(locked=locked)
                with patch('cal_bar.reminder_windows', return_value=windows):
                    CalBarApplication.show_reminder(app, self.event)
                app.sound.play.assert_not_called()

    def test_muting_an_active_reminder_stops_audio_without_dismissing_it(self):
        app = self.app(enabled=False)
        app.publish = Mock()
        CalBarApplication._setting_changed(app, app.settings, 'reminder-sound-enabled')
        app.sound.stop.assert_called_once()
        app.dismiss_reminder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
