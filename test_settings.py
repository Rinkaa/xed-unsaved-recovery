# -*- coding: utf-8 -*-
"""Headless unit tests for SettingsStore (no GUI needed).

Run: cd xed-unsaved-recovery && python3 -m unittest test_settings -v
"""

import os
import shutil
import tempfile
import unittest

from gi.repository import GLib

from unsaved_recovery import SettingsStore


class SettingsStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="unsaved-recovery-settings-test-")
        self.path = os.path.join(self._tmp, "settings.ini")
        self.store = SettingsStore(path=self.path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_defaults_when_file_missing(self):
        self.assertEqual(self.store.snapshot_delay_seconds, 2)
        self.assertEqual(self.store.sweep_interval_seconds, 30)
        self.assertEqual(self.store.max_snapshot_chars, 10_000_000)
        self.assertEqual(self.store.retention_seconds, 7 * 86400)

    def test_set_and_reload_roundtrip(self):
        self.store.set("snapshot-delay-seconds", 5)
        self.store.set("retention-days", 14)
        reloaded = SettingsStore(path=self.path)
        self.assertEqual(reloaded.snapshot_delay_seconds, 5)
        self.assertEqual(reloaded.retention_seconds, 14 * 86400)

    def test_clamp_out_of_range(self):
        self.store.set("snapshot-delay-seconds", 9999)
        self.assertEqual(self.store.snapshot_delay_seconds, 300)
        self.store.set("retention-days", 0)
        self.assertEqual(self.store.get("retention-days"), 1)

    def test_non_numeric_value_falls_back_to_default(self):
        keyfile = GLib.KeyFile()
        keyfile.set_string(SettingsStore.GROUP, "snapshot-delay-seconds", "abc")
        keyfile.save_to_file(self.path)
        store2 = SettingsStore(path=self.path)
        self.assertEqual(store2.snapshot_delay_seconds, 2)

    def test_reset_to_defaults(self):
        self.store.set("sweep-interval-seconds", 120)
        self.store.reset()
        self.assertEqual(self.store.sweep_interval_seconds, 30)

    def test_reload_if_changed_detects_external_edit(self):
        keyfile = GLib.KeyFile()
        keyfile.set_integer(SettingsStore.GROUP, "retention-days", 21)
        keyfile.save_to_file(self.path)
        # guarantee a detectable mtime change
        old = os.path.getmtime(self.path)
        os.utime(self.path, (old + 5, old + 5))
        self.store.reload_if_changed()
        self.assertEqual(self.store.get("retention-days"), 21)

    def test_reload_if_changed_noop_when_unchanged(self):
        self.store.set("retention-days", 3)
        self.store.reload_if_changed()
        self.assertEqual(self.store.get("retention-days"), 3)

    def test_reload_after_file_deletion(self):
        self.store.set("retention-days", 30)
        os.unlink(self.path)
        self.store.reload_if_changed()
        self.assertEqual(self.store.get("retention-days"), 7)


if __name__ == "__main__":
    unittest.main()
