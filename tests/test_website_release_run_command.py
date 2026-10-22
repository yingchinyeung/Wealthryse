"""Run Commands on the build VM: a name of their own each, one at a time, and none after one that was never removed."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://management.azure.com/vm/runCommands/'


def load():
    spec = importlib.util.spec_from_file_location('website_release_run_command', ROOT / 'deploy' / 'website_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAzure:
    """Run Commands that finish at once; removal finishes unless the name is in `stuck`."""

    def __init__(self, stuck=()):
        self.events = []
        self.live = set()
        self.deleting = set()
        self.stuck = set(stuck)

    @staticmethod
    def name(url):
        return url[len(BASE):].split('?')[0]

    def az(self, *args, parse=True):
        method, url = args[2], args[4]
        name = self.name(url)
        if method == 'put':
            self.events.append(('put', name, sorted(self.live | self.deleting)))
            self.live.add(name)
            return ''
        return {'properties': {'instanceView': {'executionState': 'Succeeded', 'output': 'out-' + name}}}

    def run(self, args, **kwargs):
        method, url = args[3], args[5]
        name = self.name(url)
        if method == 'delete':
            self.events.append(('delete', name))
            self.live.discard(name)
            self.deleting.add(name)
            return subprocess.CompletedProcess(args, 0, '', '')
        if name in self.deleting and name not in self.stuck:
            self.deleting.discard(name)
            return subprocess.CompletedProcess(args, 1, '', 'Not Found({"error":{"code":"ResourceNotFound"}})')
        return subprocess.CompletedProcess(args, 0, '{}', '')


class RunCommandTest(unittest.TestCase):
    def setUp(self):
        self.release = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)

    def patched(self, fake):
        return mock.patch.multiple(self.release, az=fake.az), \
            mock.patch.object(self.release.subprocess, 'run', side_effect=fake.run), \
            mock.patch.object(self.release.time, 'sleep')

    def test_each_run_command_has_its_own_name_and_starts_after_the_last_is_gone(self):
        fake = FakeAzure()
        patch_az, patch_run, patch_sleep = self.patched(fake)
        with patch_az, patch_run, patch_sleep:
            self.release.vm_run(BASE, 'website-detail-0', 'true', 'eastasia', 900)
            self.release.vm_run(BASE, 'website-detail-0', 'true', 'eastasia', 900)
            self.release.vm_run(BASE, 'website-detail-cleanup', 'true', 'eastasia', 900)
        puts = [e for e in fake.events if e[0] == 'put']
        names = [e[1] for e in puts]
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(names[0].startswith('website-detail-0-') and names[1].startswith('website-detail-0-'))
        # Nothing else existed, or was still being removed, when each one started.
        self.assertEqual([e[2] for e in puts], [[], [], []])
        self.assertEqual(fake.live | fake.deleting, set())

    def test_a_run_command_never_removed_stops_the_release_and_any_further_run(self):
        fake = FakeAzure()
        patch_az, patch_run, patch_sleep = self.patched(fake)
        with patch_az, patch_run, patch_sleep, mock.patch.object(self.release, 'REMOVE_WAIT', 0):
            with self.assertRaises(SystemExit) as stopped:
                self.release.vm_run(BASE, 'website-capture', 'true', 'eastasia', 1800)
            self.assertIn('was not removed', str(stopped.exception))
            with self.assertRaises(SystemExit) as refused:
                self.release.vm_run(BASE, 'website-detail-0', 'true', 'eastasia', 900)
            self.assertIn('never removed', str(refused.exception))
        self.assertEqual([e[0] for e in fake.events], ['put', 'delete'])

    def test_detail_cleanup_is_skipped_once_a_run_command_was_never_removed(self):
        fake = FakeAzure()
        calls = []

        def stuck_run(base, name, script, location, timeout):
            calls.append(name)
            self.release._not_removed.append(name + '-x')
            raise SystemExit('Run Command did not finish in time')

        with mock.patch.object(self.release, 'vm_run', side_effect=stuck_run):
            with self.assertRaises(SystemExit) as stopped:
                self.release.read_detail(BASE, 'eastasia', '/var/tmp/wealthryse-build-detail-abc.txt', 8504)
        self.assertEqual(str(stopped.exception), 'Run Command did not finish in time')
        self.assertEqual(calls, ['website-detail-0'])


if __name__ == '__main__':
    unittest.main()
