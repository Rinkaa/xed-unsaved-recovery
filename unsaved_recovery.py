# -*- coding: utf-8 -*-
#
# Unsaved Document Recovery for Xed
# ---------------------------------
# Snapshots any content that lives only in the buffer (not yet on disk),
# so it can be recovered after a crash or power loss.
# Covers two kinds of documents:
#   1. brand-new untitled tabs that were never saved, and
#   2. files that exist on disk but have unsaved modifications.
# Core principle: never write to any file the user owns. Snapshots go
# exclusively into this plugin's private directory; the decision of when
# a file hits disk stays entirely with the user.
#
# Behavior:
#   - ~2 s after editing stops, the full buffer text is snapshotted to
#     ~/.local/share/xed/unsaved-recovery/docs/<id>.txt (index.json).
#     The trigger condition is GtkTextBuffer.get_modified(): the buffer
#     differs from disk, so a snapshot is taken.
#   - When the user saves (buffer == disk), that snapshot is removed.
#   - Closing tabs/windows, quitting xed, power loss and crashes all keep
#     the snapshots; they are auto-cleaned after 7 days.
#   - On the next xed start, if snapshots exist, a restore dialog pops up
#     automatically; it can also be opened anytime via
#     Tools -> Restore Unsaved Documents...
#
# Debug: XED_DEBUG_UNSAVED_RECOVERY=1 xed
#
# API references (linuxmint/xed master):
#   xed/xed-window.h   -- tab-added / tab-removed / active-tab-changed,
#                         create_tab(jump_to), get_documents(),
#                         get_ui_manager()
#   xed/xed-document.h -- is_untitled(), get_short_name_for_display(),
#                         get_file().get_location(), saved/loaded signals
#   xed/xed-tab.h      -- get_document()
#   xed/resources/ui/xed-ui.xml -- /MenuBar/ToolsMenu/ToolsOps_2 placeholder
#   xed's native autosave (xed_tab_auto_save) only applies to saved
#   documents (install_auto_save_timeout requires !is_untitled) and saves
#   in place, which is the opposite of this plugin's semantics, so it is
#   not relied upon.
#
# Authors: Pi Agent(On Deepseek-v4-flash); triggered by Ikigai<kaitensekai@qq.com>
# SPDX: MIT
#

import json
import os
import sys
import time
import uuid

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")  # noqa: E402 -- xed is a GTK3 app; pin explicitly
gi.require_version("Xed", "1.0")  # noqa: E402
from gi.repository import GObject, Gtk, GLib, Xed  # noqa: E402

__all__ = ["SnapshotStore", "UnsavedRecoveryPlugin"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _debug(msg: str) -> None:
    if GLib.getenv("XED_DEBUG_UNSAVED_RECOVERY"):
        sys.stderr.write(f"[unsaved-recovery] {msg}\n")


def _preview(text: str, limit: int = 120) -> str:
    """Flatten arbitrary text into a single-line preview (whitespace collapsed)."""
    return " ".join(text.split())[:limit]


def _fmt_time(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


# ---------------------------------------------------------------------------
# Snapshot storage (pure file logic, unit-testable without a GUI)
# ---------------------------------------------------------------------------


class SnapshotStore:
    """Snapshot directory layout:

    <base>/
        index.json          {docs: [{id, title, preview, length, created, updated}]}
        docs/<id>.txt       full document text
    """

    RETENTION_SECONDS = 7 * 24 * 3600  # 7 days

    def __init__(self, base_dir=None):
        if base_dir is None:
            base_dir = os.path.join(GLib.get_user_data_dir(), "xed", "unsaved-recovery")
        self.base = base_dir
        self.docs_dir = os.path.join(self.base, "docs")
        self.index_path = os.path.join(self.base, "index.json")
        try:
            os.makedirs(self.docs_dir, exist_ok=True)
        except OSError as e:
            _debug(f"cannot create snapshot dir {self.docs_dir}: {e!r}")
        self.entries = self._load_index()

    # -- index -------------------------------------------------

    def _load_index(self) -> dict:
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
            return {
                e["id"]: e
                for e in data.get("docs", [])
                if isinstance(e, dict) and e.get("id")
            }
        except (OSError, ValueError):
            return {}

    def _save_index(self) -> None:
        try:
            tmp = self.index_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"docs": list(self.entries.values())},
                    f,
                    ensure_ascii=False,
                    indent=1,
                )
            os.replace(tmp, self.index_path)
        except OSError as e:
            _debug(f"save index failed: {e!r}")

    # -- read/write ----------------------------------------------

    def upsert(self, doc_id: str, title: str, text: str, src_path=None) -> str:
        path = os.path.join(self.docs_dir, doc_id + ".txt")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except OSError as e:
            _debug(f"write snapshot failed: {e!r}")
            return path
        now = time.time()
        entry = self.entries.get(doc_id)
        if entry is None:
            entry = {"id": doc_id, "created": now}
            self.entries[doc_id] = entry
        entry.update(
            {
                "title": title,
                "preview": _preview(text),
                "length": len(text),
                "updated": now,
                "path": src_path,
            }
        )
        self._save_index()
        return path

    def remove(self, doc_id: str) -> None:
        if doc_id in self.entries:
            del self.entries[doc_id]
        try:
            os.unlink(os.path.join(self.docs_dir, doc_id + ".txt"))
        except OSError:
            pass
        self._save_index()

    def read(self, doc_id: str):
        try:
            with open(
                os.path.join(self.docs_dir, doc_id + ".txt"), encoding="utf-8"
            ) as f:
                return f.read()
        except OSError:
            return None

    def list(self):
        """Entries sorted by last update, newest first."""
        return sorted(
            self.entries.values(), key=lambda e: e.get("updated", 0), reverse=True
        )

    def cleanup_old(self) -> int:
        """Drop snapshots older than the retention period; return count removed."""
        now = time.time()
        stale = [
            doc_id
            for doc_id, e in self.entries.items()
            if now - e.get("updated", 0) > self.RETENTION_SECONDS
        ]
        for doc_id in stale:
            self.remove(doc_id)
        return len(stale)


# ---------------------------------------------------------------------------
# Xed plugin main class
# ---------------------------------------------------------------------------


class UnsavedRecoveryPlugin(GObject.Object, Xed.WindowActivatable):
    __gtype_name__ = "UnsavedRecoveryPlugin"

    window = GObject.Property(type=Xed.Window)

    SNAPSHOT_DELAY_SECONDS = 2  # seconds after typing stops before snapshotting
    SWEEP_INTERVAL_SECONDS = 30  # safety-net sweep for missed save/remove events
    MAX_SNAPSHOT_CHARS = 10_000_000  # skip snapshots above this size (disk safety)

    def __init__(self):
        super().__init__()
        self._store = SnapshotStore()
        self._tracked = {}  # id(doc) -> {"doc", "doc_id", "timeout_src", "dirty"}
        self._handlers = []  # [(obj, handler_id)]
        self._sweep_src = None
        self._ui_merge_id = None
        self._ui_group = None

    # -- signal plumbing ------------------------------------------

    def _connect(self, obj, sig, cb):
        try:
            self._handlers.append((obj, obj.connect(sig, cb)))
        except Exception as e:  # never let a bad signal name break xed
            _debug(f"connect {sig} failed: {e!r}")

    def _disconnect_all(self):
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()

    # -- activate/deactivate ---------------------------------------

    def do_activate(self):
        try:
            self._store = SnapshotStore()
            cleaned = self._store.cleanup_old()
            _debug(f"activate: cleaned {cleaned} stale snapshots")

            win = self.window
            self._connect(win, "tab-added", self._on_tab_added)
            self._connect(win, "tab-removed", self._on_tab_removed)

            # documents already open in this window (xed has no get_tabs)
            for doc in win.get_documents():
                self._track_document(doc)

            self._sweep_src = GLib.timeout_add_seconds(
                self.SWEEP_INTERVAL_SECONDS, self._sweep
            )
            self._add_menu_item()

            # defer the recovery prompt until the window is up
            GLib.idle_add(self._maybe_prompt_recovery)
        except Exception as e:
            _debug(f"activate failed: {e!r}")

    def do_deactivate(self):
        try:
            self._flush_all()  # persist the final state before unload
            self._disconnect_all()
            if self._sweep_src is not None:
                GLib.source_remove(self._sweep_src)
                self._sweep_src = None
            self._remove_menu_item()
            for entry in self._tracked.values():
                if entry["timeout_src"] is not None:
                    GLib.source_remove(entry["timeout_src"])
            self._tracked.clear()
        except Exception as e:
            _debug(f"deactivate failed: {e!r}")

    def do_update_state(self):
        pass

    # -- document tracking -------------------------------------------

    def _on_tab_added(self, _win, tab):
        try:
            doc = tab.get_document()
            if doc is not None:
                self._track_document(doc)
        except Exception as e:
            _debug(f"tab-added: {e!r}")

    def _on_tab_removed(self, _win, tab):
        """Tab closed: untrack it, but deliberately KEEP its snapshot
        (protects against accidental closes as well as crashes)."""
        try:
            doc = tab.get_document()
            if doc is None:
                return
            entry = self._tracked.pop(id(doc), None)
            if entry and entry["timeout_src"] is not None:
                GLib.source_remove(entry["timeout_src"])
        except Exception as e:
            _debug(f"tab-removed: {e!r}")

    def _track_document(self, doc):
        if id(doc) in self._tracked:
            return
        entry = {"doc": doc, "doc_id": None, "timeout_src": None, "dirty": False}
        self._tracked[id(doc)] = entry
        self._connect(doc, "changed", lambda _d, e=entry: self._on_changed(e))
        self._connect(doc, "saved", lambda _d, e=entry: self._on_saved(e))
        self._connect(doc, "loaded", lambda _d, e=entry: self._on_saved(e))

    def _on_changed(self, entry):
        """Buffer changed: schedule a snapshot for any doc with unsaved edits."""
        entry["dirty"] = True
        if entry["timeout_src"] is None:
            entry["timeout_src"] = GLib.timeout_add_seconds(
                self.SNAPSHOT_DELAY_SECONDS, self._flush, entry
            )

    def _on_saved(self, entry):
        """Document was saved (buffer == disk): drop its snapshot."""
        doc = entry["doc"]
        try:
            if not doc.get_modified() and entry["doc_id"] is not None:
                _debug("document saved, removing snapshot")
                self._store.remove(entry["doc_id"])
                entry["doc_id"] = None
                entry["dirty"] = False
        except Exception as e:
            _debug(f"on_saved: {e!r}")

    def _flush(self, entry):
        """Debounced callback: write a snapshot when buffer != disk, else drop it."""
        entry["timeout_src"] = None
        if not entry["dirty"]:
            return False
        entry["dirty"] = False
        doc = entry["doc"]
        try:
            if not doc.get_modified():
                # nothing unsaved (just saved / undid back to clean) -> no snapshot needed
                if entry["doc_id"] is not None:
                    self._store.remove(entry["doc_id"])
                    entry["doc_id"] = None
                return False
            text = doc.get_text(doc.get_start_iter(), doc.get_end_iter(), False)
            if len(text) > self.MAX_SNAPSHOT_CHARS:
                _debug("document too large, skip snapshot")
                return False
            if entry["doc_id"] is None:
                entry["doc_id"] = uuid.uuid4().hex[:12]
            self._store.upsert(
                entry["doc_id"],
                self._doc_title(doc),
                text,
                src_path=self._doc_path(doc),
            )
        except Exception as e:
            _debug(f"flush failed: {e!r}")
        return False

    def _flush_all(self):
        for entry in list(self._tracked.values()):
            if entry["timeout_src"] is not None:
                GLib.source_remove(entry["timeout_src"])
                entry["timeout_src"] = None
            self._flush(entry)

    def _sweep(self):
        """Safety net: clean up missed saved-but-still-snapshotted docs and
        flush any dirty docs whose debounce timer was lost."""
        try:
            for entry in list(self._tracked.values()):
                doc = entry["doc"]
                if not doc.get_modified() and entry["doc_id"] is not None:
                    self._store.remove(entry["doc_id"])
                    entry["doc_id"] = None
                    entry["dirty"] = False
                elif entry["dirty"]:
                    self._flush(entry)
        except Exception as e:
            _debug(f"sweep failed: {e!r}")
        return True  # keep the timer alive

    @staticmethod
    def _doc_title(doc) -> str:
        try:
            name = doc.get_short_name_for_display()
            if name:
                return name
        except Exception:
            pass
        return "Unsaved Document"

    @staticmethod
    def _doc_path(doc):
        """Return the on-disk path for saved files, None for untitled docs."""
        try:
            loc = doc.get_file().get_location()
            return loc.get_path() if loc is not None else None
        except Exception:
            return None

    # -- menu ---------------------------------------------------

    MENU_XML = """
    <ui>
      <menubar name="MenuBar">
        <menu action="Tools">
          <placeholder name="ToolsOps_2">
            <menuitem action="RestoreUnsavedDocs"/>
          </placeholder>
        </menu>
      </menubar>
    </ui>
    """

    def _add_menu_item(self):
        try:
            ui = self.window.get_ui_manager()
            if ui is None:
                return
            self._ui_group = Gtk.ActionGroup(name="UnsavedRecoveryActions")
            action = Gtk.Action(
                name="RestoreUnsavedDocs",
                label="Restore Unsaved Documents...",
                tooltip="Recover documents that were not saved before a crash or power loss",
            )
            action.connect("activate", lambda _a: self._show_recovery_dialog())
            self._ui_group.add_action_with_accel(action, None)
            ui.insert_action_group(self._ui_group, -1)
            self._ui_merge_id = ui.add_ui_from_string(self.MENU_XML)
            if self._ui_merge_id == 0:
                _debug("menu merge failed")
        except Exception as e:
            _debug(f"add menu item failed: {e!r}")

    def _remove_menu_item(self):
        try:
            ui = self.window.get_ui_manager()
            if ui is None:
                return
            if self._ui_merge_id is not None:
                ui.remove_ui(self._ui_merge_id)
                self._ui_merge_id = None
            if self._ui_group is not None:
                ui.remove_action_group(self._ui_group)
                self._ui_group = None
        except Exception as e:
            _debug(f"remove menu item failed: {e!r}")

    # -- restore dialog --------------------------------------------

    def _maybe_prompt_recovery(self):
        try:
            if self._store is not None and self._store.list():
                self._show_recovery_dialog()
        except Exception as e:
            _debug(f"prompt failed: {e!r}")
        return False

    def _show_recovery_dialog(self):
        try:
            entries = self._store.list()
        except Exception as e:
            _debug(f"dialog: {e!r}")
            return

        if not entries:
            _debug("no snapshots to restore")
            return

        dlg = Gtk.Dialog(
            title="Restore Unsaved Documents",
            transient_for=self.window,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            "Delete Selected",
            Gtk.ResponseType.REJECT,
            "Ignore",
            Gtk.ResponseType.CANCEL,
            "Restore Selected",
            Gtk.ResponseType.ACCEPT,
        )

        model = Gtk.ListStore(
            str, str, str, str, str
        )  # id, title, preview, updated, path
        view = Gtk.TreeView(model=model)
        view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)

        def add_column(title_text, col_idx, expand=False):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title_text, renderer, text=col_idx)
            col.set_expand(expand)
            col.set_resizable(True)
            view.append_column(col)

        add_column("Document", 1, expand=False)
        add_column("Original Path", 4, expand=False)
        add_column("Content Preview", 2, expand=True)
        add_column("Last Snapshot", 3, expand=False)

        def rebuild():
            model.clear()
            for e in self._store.list():
                model.append(
                    (
                        e["id"],
                        e.get("title", "Unsaved Document"),
                        e.get("preview", ""),
                        _fmt_time(e.get("updated", 0)),
                        e.get("path") or "-",
                    )
                )

        rebuild()

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(300)
        scroll.set_min_content_width(560)
        scroll.add(view)
        dlg.vbox.pack_start(scroll, True, True, 0)
        dlg.show_all()

        while True:
            resp = dlg.run()
            if resp == Gtk.ResponseType.ACCEPT:
                self._restore_selected(view, model)
                dlg.destroy()
                break
            elif resp == Gtk.ResponseType.REJECT:
                self._delete_selected(view, model)
                if not self._store.list():
                    dlg.destroy()
                    break
                rebuild()
            else:
                dlg.destroy()
                break

    def _selected_ids(self, view, model):
        _model, paths = view.get_selection().get_selected_rows()
        return [model[p][0] for p in paths]

    def _delete_selected(self, view, model):
        for doc_id in self._selected_ids(view, model):
            try:
                self._store.remove(doc_id)
                _debug(f"snapshot deleted: {doc_id}")
            except Exception as e:
                _debug(f"delete failed: {e!r}")

    def _restore_selected(self, view, model):
        restored = 0
        for doc_id in self._selected_ids(view, model):
            try:
                text = self._store.read(doc_id)
                if text is None:
                    continue
                tab = self.window.create_tab(True)
                doc = tab.get_document()
                doc.set_text(text)
                # create_tab may not emit tab-added; track it explicitly
                self._track_document(doc)
                # restore consumed: the old snapshot is removed and the new
                # untitled tab keeps being snapshotted until the user saves
                self._store.remove(doc_id)
                restored += 1
            except Exception as e:
                _debug(f"restore failed for {doc_id}: {e!r}")
        if restored:
            info = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=f"Restored {restored} unsaved document(s).",
            )
            info.format_secondary_text(
                "Recovered content is placed in a new untitled tab "
                "(never written back to the original file).\n"
                "If the snapshot came from a file on disk, save it to "
                "the original path shown in the list.\n"
                "Restored tabs keep being snapshotted until you save them."
            )
            info.run()
            info.destroy()
