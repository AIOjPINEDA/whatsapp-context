# Contributing

This project reads private conversations, so a few rules come first.

- **Read-only forever.** No sending, reacting, marking as read, unofficial protocols or UI automation.
- **The helper stays minimal.** Every change to `helper/main.swift` forces users to re-grant Full Disk
  Access; say so at the top of the PR.
- **Never share real data**: no real chats, names, numbers, screenshots or exports in issues, PRs, tests
  or logs. Use invented data (`Pablo Prueba`, `+34 600 000 001`). Security problems: [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync --locked
```

```bash
make test
```

`make test` builds the helper, lints and runs pytest on a synthetic WhatsApp container; it needs no
permissions. If you use the tool on the same Mac, install with `INSTALLED=<temp dir>/WAExport.app` and
test `scripts/register.py` with `HOME` in a temp dir. Keep it simple: new behaviour comes with a test,
files stay under their line ceilings, and coding agents read [AGENTS.md](AGENTS.md).
Semantic commits, one topic per PR. Contributions are released under the [MIT License](LICENSE).
