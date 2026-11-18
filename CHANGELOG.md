# Changelog

## 0.3.0

**This release changes WAExport.** After `git pull`, run `make setup`; at its Full Disk Access step,
remove WAExport from the list (-) and drag it in again (`make grant` does only that step). Restart your
assistants **before** adding `transcribe` to the policy: a server still running 0.2 rejects the new key.

- Voice notes can be transcribed on the Mac with Apple's SpeechTranscriber (macOS 26 or later): no
  network, off until `[options] transcribe = "<language>"` names a language. New `wa transcribe`
  (`--install` downloads the language model once) and MCP tool `whatsapp_transcribe`.
- WAExport has a second verb, `audio`: it copies only the requested voice notes (Ogg Opus or M4A, up to
  16 MB) of allowed chats inside the policy's days, rejecting other paths, links and files. The copies are
  deleted right after transcribing; only the text is cached in `transcripts.sqlite` (`0600`), pruned after
  every export so revoked, excluded or expired chats lose their transcripts too.
- `build/wa-transcribe`, the transcriber, has no Full Disk Access: it only reads the copies it is given.
  It needs Command Line Tools 26 or later; with older ones `make build` skips it and builds the rest.
- WAExport checks both ends of every copy on the open file (`F_GETPATH`), so a folder swapped for a link
  cannot make it read outside WhatsApp's container or write outside `audio/`; it never waits on a FIFO
  and writes nothing after 100 s. It reads only from WhatsApp's own container (pinned by descriptor), and
  never more than 16 MB per note. Transcriptions run one at a time, and a revoke exported meanwhile wins.
- A transcript belongs to one message (its ID and stanza ID): a restored database reusing an ID does not
  inherit another chat's text.
- Messages show a voice note's length and whether it is transcribed, not downloaded or pending; search
  also looks inside transcripts.
- WAExport reports version 0.3.0.

## 0.2.1

**This release changes WAExport.** After `git pull`, run `make setup`; at its Full Disk Access step,
remove WAExport from the list (-) and drag it in again.

- WAExport is built for macOS 14 or later, the minimum the app declares, and for the Mac's own CPU even
  from a Rosetta terminal. Before, `swiftc` used its own default minimum: the Command Line Tools
  (Swift 6.2) on macOS 27 stamp macOS 28.0, so macOS refused to open WAExport with "You can't use this
  version of the application" (`error -10825`). `wa` now explains that error too.
- `make grant`: the Full Disk Access step on its own. macOS lets no app request this permission, so it
  opens the list and a Finder window with WAExport, copies the app's path, and waits until you switch it
  on: nothing to press in the Terminal. `make setup` uses it, and `wa` points to it when access is missing.
- WAExport reports version 0.2.1 (`--version`, `status.json`).

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
