# -*- coding: utf-8 -*-
"""Headless unit tests for SnapshotStore (no GUI needed).

Run: cd xed-unsaved-recovery && python3 -m unittest test_storage -v
"""

import os
import shutil
import tempfile
import time
import unittest

from unsaved_recovery import SnapshotStore, _preview


class SnapshotStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="unsaved-recovery-test-")
        self.store = SnapshotStore(base_dir=self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_upsert_writes_file_and_index(self):
        path = self.store.upsert(
            "doc1", "Untitled Document 1", "line one\nline two\nline three"
        )
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "line one\nline two\nline three")
        self.assertIn("doc1", self.store.entries)
        self.assertEqual(self.store.entries["doc1"]["title"], "Untitled Document 1")
        self.assertEqual(self.store.entries["doc1"]["length"], 28)  # 8+1+8+1+10

    def test_upsert_stores_src_path(self):
        self.store.upsert("doc2", "foo.lua", "x = 1", src_path="/home/user/x.lua")
        self.assertEqual(self.store.entries["doc2"]["path"], "/home/user/x.lua")
        self.store.upsert("doc3", "Untitled", "y = 2")  # src_path default None
        self.assertIsNone(self.store.entries["doc3"]["path"])

    def test_utf8_roundtrip(self):
        # multibyte content (not CJK) must survive the snapshot round-trip
        text = "Grüße aus Zürich 🎉\nnaïve café résumé"
        self.store.upsert("doc1", "t", text)
        self.assertEqual(self.store.read("doc1"), text)
        self.assertEqual(self.store.entries["doc1"]["length"], len(text))

    def test_upsert_update_replaces_content(self):
        self.store.upsert("doc1", "t", "old")
        self.store.upsert("doc1", "t", "new content")
        self.assertEqual(self.store.read("doc1"), "new content")
        self.assertEqual(self.store.entries["doc1"]["length"], 11)

    def test_remove_deletes_file_and_entry(self):
        self.store.upsert("doc1", "t", "hello")
        self.store.remove("doc1")
        self.assertNotIn("doc1", self.store.entries)
        self.assertFalse(os.path.exists(self.store.docs_dir + "/doc1.txt"))

    def test_read_missing_returns_none(self):
        self.assertIsNone(self.store.read("nope"))

    def test_cleanup_old_removes_stale_only(self):
        self.store.upsert("fresh", "t", "a")
        self.store.upsert("stale", "t", "b")
        self.store.entries["stale"]["updated"] = time.time() - 8 * 24 * 3600
        self.store._save_index()
        removed = self.store.cleanup_old()
        self.assertEqual(removed, 1)
        self.assertIn("fresh", self.store.entries)
        self.assertNotIn("stale", self.store.entries)

    def test_list_sorted_by_updated_desc(self):
        self.store.upsert("old", "t", "a")
        time.sleep(0.01)
        self.store.upsert("new", "t", "b")
        self.store.entries["old"]["updated"] = 1.0
        self.store._save_index()
        ids = [e["id"] for e in self.store.list()]
        self.assertEqual(ids, ["new", "old"])

    def test_reload_from_disk(self):
        self.store.upsert(
            "doc1",
            "Untitled Document",
            "text before power loss",
            src_path="/tmp/me.txt",
        )
        store2 = SnapshotStore(base_dir=self._tmp)
        self.assertIn("doc1", store2.entries)
        self.assertEqual(store2.read("doc1"), "text before power loss")
        self.assertEqual(store2.entries["doc1"]["path"], "/tmp/me.txt")

    def test_preview_flattens_whitespace(self):
        self.assertEqual(
            _preview("line one\n  line two\n\nline three", 120),
            "line one line two line three",
        )
        self.assertEqual(len(_preview("x" * 500, 120)), 120)


if __name__ == "__main__":
    unittest.main()
