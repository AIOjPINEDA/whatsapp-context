# Security

whatsapp-context gives an AI assistant access to private conversations. This is what it protects, what
it does not, and how to report a problem.

## Guarantees

- **WhatsApp's data is never modified.** The helper opens `ChatStorage.sqlite` read-only
  (`mode=ro&readonly_shm=1`, or `immutable=1` without a WAL).
- **One program holds Full Disk Access:** `/Applications/WAExport.app`, about 290 lines of Swift with no
  network and no child processes. The MCP server and `wa` launch it through `open` and run unprivileged.
- **A misplaced permission is visible.** If the process running the server or `wa` can read WhatsApp
  itself, the output starts with a `WARNING:` line.
- **Only allowed chats carry text.** The export indexes every chat without message text, drafts or
  media URLs and keys; `[exclude]` chats are hidden from every MCP tool and `wa` view, even as metadata, and the chat tools answer
  as if they did not exist (their name stays in the local export index).
- **Private files.** The export folder is `0700`; policy files are written `0600`; all are git-ignored.
- **It cannot send.** No send tool, no automation, no unofficial protocol.
- **Policy changes from the chat** go only to `config/policy-claude.toml` and cannot lift an exclusion.

## Not protected

- **Anyone using your macOS account, or any process running as you**, including assistants with shell
  access: they can read the export and the policy directly. The policy filters the MCP tools only.
- **Your assistant's provider** receives whatever the assistant reads, under its own terms.
- **Prompt injection.** Anyone who can message you can write text aimed at an AI. The server marks
  message text as untrusted, but keep tool approvals on, and be careful combining this with tools that
  send email, post or run commands.
- **Remote exposure.** The server is local (stdio). Do not tunnel it to the internet.

## Reporting a vulnerability

Don't open a public issue. Use GitHub private vulnerability reporting: the repo's **Security** tab >
**Report a vulnerability**. Include the commit, macOS version, steps and impact, with synthetic data only:
never real chats, names, numbers or exports. Only the latest commit on the default branch is supported.
