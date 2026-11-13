<p align="center"><img src="docs/assets/banner.png" alt="whatsapp-context" width="100%"></p>

**Let Claude, ChatGPT or any local AI assistant read the WhatsApp chats you choose. Read-only, on your Mac.**

![macOS 14+](https://img.shields.io/badge/macOS-14%2B-555555) ![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB) ![MCP stdio](https://img.shields.io/badge/MCP-stdio-6366f1) ![License MIT](https://img.shields.io/badge/license-MIT-blue)

[Español](README.es.md) · [Website](https://aiojpineda.github.io/whatsapp-context/) · [Security](SECURITY.md)

## What you can ask

- *"Prioritise my WhatsApp from the last 24 hours. What needs a reply from me?"*
- *"Catch me up on the Kitchen renovation group since Monday."*
- *"Find the plumber's quote someone sent and summarise the thread around it."*

The assistant suggests replies as text. You send them yourself: there is no send tool.

## How it works

```text
WhatsApp for Mac ──▶ ChatStorage.sqlite (protected by macOS)
                          │ read-only
                  WAExport.app  ← the only program with Full Disk Access
                          │ export: index of every chat + text of allowed chats only
       MCP server + wa CLI (Python, no special permissions) ◀── config/policy.toml
                          │ stdio
          Claude Code · Claude Desktop · ChatGPT / Codex · any local MCP client
```

The export (`~/.local/share/whatsapp-context`) refreshes itself after 15 minutes or a policy change.

## Install

Requirements:
- macOS 14 or later
- WhatsApp for Mac, signed in and synced
- Xcode Command Line Tools (`xcode-select --install`)
- [uv](https://docs.astral.sh/uv/) (`brew install uv`); it installs Python 3.12 if needed

```bash
git clone https://github.com/AIOjPINEDA/whatsapp-context.git ~/whatsapp-context
```

```bash
cd ~/whatsapp-context
```

```bash
make setup
```

`make setup` checks requirements, installs `/Applications/WAExport.app`, creates `config/policy.toml`,
opens System Settings so **you** switch on Full Disk Access for WAExport (**only** WAExport: never
Terminal or an AI app) and offers to connect each assistant it finds. It is safe to run again. If your
user cannot write to `/Applications`, it asks you to drag WAExport there in Finder.

macOS never lets an app ask for Full Disk Access, so that one switch is yours: drag WAExport from the
Finder window that opens into the list and turn it on. The installer notices by itself and carries on.
To do only this step again (after an update, say), run `make grant`.

## Install with your AI agent

Paste this into Claude Code, Codex or any agent that can run commands on your Mac:

```text
Clone https://github.com/AIOjPINEDA/whatsapp-context into ~/whatsapp-context and follow its INSTALL.md step by step. Ask me before every step that needs my action.
```

[INSTALL.md](INSTALL.md) is the agent's runbook. It stops for anything only you can do and never picks chats for you.

## Choose what it can read

As shipped, the assistant reads **no** message text, only chat names, times and unread counts. Find exact
names with `uv run wa chats --all --grep kitchen` (each chat shows its `[JID]`), then edit
`config/policy.toml`:

```toml
[content]
chats = ["Book club", "+34 600 000 000"]   # exact names, phone numbers or JIDs
people = []                                # groups where this person is active

[projects."Kitchen renovation"]
context = "~/Documents/Kitchen"            # folder (its README.md) or file the assistant reads
chats = ["Kitchen renovation crew"]
patterns = ['^Kitchen ']                   # regex on chat names

[exclude]
chats = ["Family"]                         # never shown, not even as metadata

[options]
days = 180
```

- Precedence: `exclude` > `chats` > `patterns` > `people`. Case and extra spaces are ignored.
- A name shared by several chats grants nothing (unless they are one person's phone and linked ID):
  use the phone number or the JID.
- Check with `uv run wa status`: it lists `Policy entry with no match` and `Policy conflict` lines.
- Asking from the chat (*"give access to the group X in project Y"*, *"revoke X"*, *"exclude X"*) writes
  only to `config/policy-claude.toml`, by JID with the name as a comment, and can never lift your
  exclusions. Revoke tells you if another rule still grants the chat.
- Search returns 50 results, most recent first, and says when it stopped; raise it with
  `uv run wa search "quote" --limit 200`.

## Supported assistants

| Assistant | Register with |
|---|---|
| Claude Code | `make register-code` |
| Claude Desktop | `make register-desktop` (quit the app with Cmd+Q when asked) |
| ChatGPT for Mac, Codex CLI and IDE extension | `make register-codex`, then restart the app |
| Any other local MCP client | `make show-mcp` and paste the JSON into that client |

claude.ai and chatgpt.com in the browser need remote servers; this one is local on purpose.

## Privacy & security

- **Read-only.** WAExport opens WhatsApp's database read-only, has no network and cannot send anything.
- **Only allowed chats carry text.** Everything else is metadata; the index never holds last-message
  text, drafts or media keys. `[exclude]` chats are hidden from every tool and `wa` view (their name stays
  in the local export index).
- **Your assistant's provider receives whatever the assistant reads**, like anything else you show it.
- **The policy filters the MCP tools, not your account.** An assistant with shell access runs as you and
  could read the export directly. Message text is untrusted: keep tool approvals on.

Threat model and reporting: [SECURITY.md](SECURITY.md).

## Update, uninstall, troubleshooting

Update: `git pull` in `~/whatsapp-context`, then `make setup` and restart your assistants. If the
[CHANGELOG](CHANGELOG.md) says WAExport changed, re-grant Full Disk Access (see below).

Uninstall: quit Claude Desktop, then `make uninstall`. It removes WAExport, the export and this repo's
client entries; if an entry cannot be removed it says so, exits with an error and keeps WAExport and the export.
Then remove WAExport from Full Disk Access and delete the folder.

| You see | Do this |
|---|---|
| `WAExport has no Full Disk Access` | `make grant`: it opens the list and Finder, and waits until you switch WAExport on |
| It stopped working after an update | WAExport is signed ad hoc, so a new build loses the grant: remove it (-) and add it again (+) |
| *You can't use this version of the application "WAExport"* or `error -10825` | Built before 0.2.1: `git pull`, then `make setup` |
| `(is WhatsApp open on this Mac?)` | Open WhatsApp for Mac and let it sync |
| `Policy entry with no match` | Copy the exact name from `uv run wa chats --all`; if *ambiguous*, use the phone number or `[JID]` |
| `WARNING: the process running this tool ...` (always the first line) | Remove Full Disk Access from that app; only WAExport should have it |
| Wrong time zone | Set `WA_TZ`, e.g. `Europe/Madrid` |

## FAQ

- **Can it send messages?** No, and it never will.
- **Does it link a device or use the WhatsApp API?** No. It reads what WhatsApp for Mac already stored.
- **Windows, Linux, iPhone or Android?** No. macOS with WhatsApp for Mac only.
- **Will a WhatsApp update break it?** Possibly; `uv run wa status` names what changed.
- **Can the assistant grant itself more chats?** Only with `whatsapp_allow`, which your client should ask you to approve each time.
- **Can it find a chat the inbox no longer shows?** Yes: `whatsapp_chats` with `all` (and `grep`) lists every chat.

## License

[MIT](LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

Independent project, not affiliated with or endorsed by WhatsApp or Meta. Trademarks belong to their
owners. Use it with your own account and respect the privacy of the people who write to you.
