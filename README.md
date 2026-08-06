# xed-unsaved-recovery

An Xed (Linux Mint text editor) plugin that **snapshots everything that
exists only in the buffer — not yet on disk** — so it can be recovered after
a crash or power loss.

Authors: Pi Agent(On Deepseek-v4-flash); triggered by Ikigai<kaitensekai@qq.com>

## Design principles

- **Never writes to any file you own on disk.** Snapshots go exclusively
  into the plugin's private directory `~/.local/share/xed/unsaved-recovery/`;
  when a file actually hits disk stays entirely up to you.
- Covers two kinds of documents:
  1. brand-new untitled tabs that were never saved;
  2. files that exist on disk but have unsaved modifications
     (**never written back in place** — only snapshotted).
- Trigger condition: `GtkTextBuffer.get_modified()` — a snapshot exists iff
  the buffer differs from disk.

## Behavior

| Event | Behavior |
| --- | --- |
| Any document's content changes | ~2 s after typing stops, full text is snapshotted to `docs/<id>.txt` (index in `index.json`) |
| User saves the document (buffer == disk) | that document's snapshot is removed |
| Closing tabs / windows / quitting xed | snapshots are kept (protects against accidental closes too) |
| Power loss / crash | snapshots are kept (at most the last ~2 s of typing are lost) |
| Next xed start | restore dialog pops up automatically if snapshots exist; also available anytime via **Tools → Restore Unsaved Documents...** |
| Snapshots older than 7 days | auto-cleaned |

To restore: select entries in the dialog → **Restore Selected**; content is
placed in new untitled tabs (never written back to the original file). The
dialog shows the original path for disk files so you can save them back.
Restored tabs keep being snapshotted until you save them.

## Installation

```bash
mkdir -p ~/.local/share/xed/plugins/xed-unsaved-recovery
cp unsaved_recovery.py unsaved_recovery.plugin ~/.local/share/xed/plugins/xed-unsaved-recovery/
```

Restart xed → **Edit → Preferences → Extensions** → enable
*Unsaved Document Recovery*.

## Verification

1. Create a new untitled tab, type some text, wait ~3 s;
2. `ls ~/.local/share/xed/unsaved-recovery/docs/` should show a `.txt` file;
3. `kill -9 $(pgrep xed)` to simulate a crash;
4. Reopen xed → the "Restore Unsaved Documents" dialog should appear →
   restore the selection → your text is back;
5. Open an existing file, make a few edits without saving, wait ~3 s → a
   snapshot appears while the original file remains untouched;
6. Ctrl+S → the snapshot disappears.

## Debug

```bash
XED_DEBUG_UNSAVED_RECOVERY=1 xed
```

## Tests

```bash
GI_TYPELIB_PATH=/usr/lib/x86_64-linux-gnu/xed/girepository-1.0 \
LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/xed \
python3 -m unittest test_storage -v
```

## Limitations

- Documents larger than 10,000,000 characters are not snapshotted (disk
  safety; noted in the debug log).
- Snapshots are full-text copies taken ~2 s after typing stops, not
  keystroke logs — at most the last ~2 s of input can be lost.
- Restored content lands in new untitled tabs; save it to the original path
  yourself when needed.
