"""The updater wrapper must not change any client's transport or lifecycle."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mudweave


class LauncherTests(unittest.TestCase):
    def test_help_never_checks_or_spawns(self):
        with patch.object(mudweave.updates, 'start_check') as check, patch.object(mudweave.subprocess, 'Popen') as spawn, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mudweave.main(['--help']), 0)
        check.assert_not_called()
        spawn.assert_not_called()

    def test_broker_preserves_control_path_and_protocol_stdio(self):
        child = Mock()
        child.wait.return_value = 7
        args = ['--control', 'private home/control.json', 'mcp']
        with patch.object(mudweave.subprocess, 'Popen', return_value=child) as spawn, patch.object(mudweave.updates, 'start_check') as check, contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(mudweave.main(['broker', *args]), 7)
        spawn.assert_called_once_with([sys.executable, '-m', 'mudgpt', *args])
        check.assert_called_once_with()
        self.assertEqual(stdout.getvalue(), '')

    def test_launch_before_check_failure_does_not_break_child(self):
        events = []
        child = Mock()
        child.wait.return_value = 0
        def launch(args):
            events.append('launched')
            return child
        def failure():
            self.assertEqual(events, ['launched'])
            raise OSError('synthetic failure')
        with patch.object(mudweave.subprocess, 'Popen', side_effect=launch), patch.object(mudweave.updates, 'start_check', side_effect=failure):
            self.assertEqual(mudweave.main(['run', '--', '-m', 'muse.adapter', 'serve']), 0)

    def test_opt_out_has_no_update_call(self):
        child = Mock()
        child.wait.return_value = 0
        with patch.object(mudweave.subprocess, 'Popen', return_value=child) as spawn, patch.object(mudweave.updates, 'start_check') as check:
            self.assertEqual(mudweave.main(['--no-update-check', 'run', '--', 'private home/mailbox_courier.py']), 0)
        spawn.assert_called_once_with([sys.executable, 'private home/mailbox_courier.py'])
        check.assert_not_called()

    def test_short_game_command_never_checks(self):
        child = Mock()
        child.wait.return_value = 0
        for arguments in (['status'], ['--control', 'private/control.json', 'send', 'look'], ['--control=private/control.json', 'observe']):
            with self.subTest(arguments=arguments), patch.object(mudweave.subprocess, 'Popen', return_value=child), patch.object(mudweave.updates, 'start_check') as check:
                self.assertEqual(mudweave.main(['broker', *arguments]), 0)
            check.assert_not_called()

    def test_interrupt_does_not_takeover_or_kill(self):
        child = Mock()
        child.wait.side_effect = [KeyboardInterrupt, 0]
        with patch.object(mudweave.subprocess, 'Popen', return_value=child), patch.object(mudweave.updates, 'start_check'):
            self.assertEqual(mudweave.main(['broker', 'serve']), 0)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_signal_exit_and_update_routing(self):
        child = Mock()
        child.wait.return_value = -15
        with patch.object(mudweave.subprocess, 'Popen', return_value=child), patch.object(mudweave.updates, 'start_check'):
            self.assertEqual(mudweave.main(['broker', 'serve']), 143)
        with patch.object(mudweave.updates, 'main', return_value=0) as updater:
            self.assertEqual(mudweave.main(['update', 'check', '--json']), 0)
        updater.assert_called_once_with(argv=['check', '--json'])


if __name__ == '__main__':
    unittest.main()
