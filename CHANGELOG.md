# Changelog

## 0.2.0

**This release changes WAExport.** After `make setup`, open System Settings > Privacy & Security > Full
Disk Access, remove WAExport (-) and add `/Applications/WAExport.app` again (+).

- The export never contains last-message text, drafts or media URLs and keys: they are copied as empty
  values instead of being written and then cleared. It also keeps the indexes queries need.
- Chat-driven edits (`whatsapp_allow`, `whatsapp_revoke`, `whatsapp_exclude`) save the chat's JID with
  its name as a comment, keep every key of the file, and run one at a time under a file lock.
- A name shared by several chats grants nothing unless they are one person's phone and linked ID; use
  the phone number or JID. Revoke tells you which rule still grants a chat; exclude checks it worked.
- `uv run wa chats --all` always shows each chat's JID; `whatsapp_chats` can list every chat (`all`,
  `grep`).
- Search says when it stops at its limit; `uv run wa search` has `--limit` (default 50). A chat outside
  the given project is an error instead of being searched.
- The Full Disk Access `WARNING:` comes first in every `wa` and MCP answer, errors included. MCP errors
  are reported as tool errors.
- The export is refreshed when the policy's content changes, not its timestamp, and a failed refresh is
  never reported as fresh. The export folder is made private (`0700`) and a symlinked one is refused.
- `make setup` checks for macOS 14 or later and, when `/Applications` is not writable, asks you to copy
  WAExport there in Finder.
- `make uninstall` exits with an error and keeps WAExport and the export if a client registration
  could not be removed. Codex entries are removed reliably whatever their key order or quoting.

## 0.1.0

First release.
