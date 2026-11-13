"""Register the whatsapp MCP server (stdio) in local clients, or remove it from all of them:

    uv run python -I scripts/register.py claude-code | claude-desktop | codex | show | remove

An existing "whatsapp" entry is replaced or removed only when it runs this repo's Python. `codex` covers
the ChatGPT app, Codex CLI and IDE extension (~/.codex/config.toml)."""

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from whatsapp_context.paths import REPO, write_private

COMMAND = str(REPO / ".venv" / "bin" / "python")
ARGS = ["-I", "-m", "whatsapp_context.mcp_server"]
ENTRY = {"command": COMMAND, "args": ARGS}
DESKTOP = Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
CODEX = Path("~/.codex/config.toml").expanduser()
LOG = Path("~/.local/share/whatsapp-context/register-desktop.log").expanduser()
CODEX_TABLE = f"[mcp_servers.whatsapp]\ncommand = {json.dumps(COMMAND)}\nargs = {json.dumps(ARGS)}\n"


def _ours(entry: object) -> bool:
    return isinstance(entry, dict) and entry.get("command") == COMMAND


def _foreign(where: str) -> str:
    return f'{where} already has a "whatsapp" server that is not this repo\'s; left it alone.'


def _save(path: Path, text: str) -> None:
    """Keep the first backup (the file as it was before this tool touched it), then write 0600."""
    path = path.resolve()  # keep a dotfiles symlink intact: write its target
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".bak-whatsapp")
    if path.exists() and not backup.exists():
        write_private(backup, path.read_text())
    write_private(path, text)


def desktop_running() -> bool:
    """Claude Desktop rewrites its config from memory while it runs: only write it while it is closed."""
    # pgrep does not see the app from inside Claude Code sessions; ps does.
    out = subprocess.run(["/bin/ps", "-axo", "ucomm="], capture_output=True, text=True).stdout
    return "Claude" in (line.strip() for line in out.splitlines())


def write_desktop(remove: bool = False) -> str:
    data = json.loads(DESKTOP.read_text()) if DESKTOP.exists() else {}
    servers = data.setdefault("mcpServers", {})
    entry = servers.get("whatsapp")
    if entry is not None and not _ours(entry):
        return _foreign(str(DESKTOP))
    if remove and entry is None:
        return "Claude Desktop: nothing to remove."
    if remove:
        del servers["whatsapp"]
    else:
        servers["whatsapp"] = ENTRY
    _save(DESKTOP, json.dumps(data, indent=2, ensure_ascii=False))
    return f"{'Removed from' if remove else 'Registered in'} Claude Desktop ({DESKTOP})."


def wait_detached() -> str:
    """Double fork so the waiter survives Claude Desktop quitting (it hosts the caller)."""
    if os.fork():
        return ("Quit Claude Desktop with Cmd+Q: the server is registered once it closes, and the app reopens.\n"
                f"Progress is logged to {LOG}.")
    os.setsid()
    if os.fork():
        os._exit(0)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as log:
        deadline = time.time() + 15 * 60
        while desktop_running() and time.time() < deadline:
            time.sleep(1)
        if desktop_running():
            log.write(f"{time.ctime()}: Claude Desktop still open after 15 minutes; nothing was changed.\n")
            os._exit(1)
        time.sleep(2)  # let the app finish its last config write
        try:
            log.write(f"{time.ctime()}: {write_desktop()}\n")
        except (OSError, ValueError) as exc:
            log.write(f"{time.ctime()}: could not register: {exc}\n")
        subprocess.run(["/usr/bin/open", "-a", "Claude"])
    os._exit(0)


def claude_desktop(remove: bool = False) -> str:
    if not desktop_running():
        return write_desktop(remove)
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "claude-desktop" and not remove:
        return wait_detached()
    raise RuntimeError("Claude Desktop is open: quit it with Cmd+Q, then run this again.")


def claude_code(remove: bool = False) -> str:
    claude = shutil.which("claude")
    if not claude:
        return "Claude Code: `claude` is not on the PATH; skipped."
    current = subprocess.run([claude, "mcp", "get", "whatsapp"], capture_output=True, text=True)
    if current.returncode == 0 and COMMAND not in current.stdout:
        return _foreign("Claude Code")
    if current.returncode == 0:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", "whatsapp"], capture_output=True)
    if remove:
        return "Removed from Claude Code." if current.returncode == 0 else "Claude Code: nothing to remove."
    if subprocess.run([claude, "mcp", "add", "--scope", "user", "whatsapp", "--", COMMAND, *ARGS]).returncode:
        raise RuntimeError("`claude mcp add` failed.")
    return "Registered in Claude Code (user scope)."


def codex(remove: bool = False) -> str:
    text = CODEX.read_text() if CODEX.exists() else ""
    data = tomllib.loads(text)  # invalid TOML aborts here, before anything is written
    entry = data.get("mcp_servers", {}).get("whatsapp")
    if entry is not None and not _ours(entry):
        return _foreign(str(CODEX))
    if not remove and entry is not None:
        return f"{CODEX} already has this server."
    if remove and entry is None:
        return f"{CODEX}: nothing to remove."
    new = text.replace(CODEX_TABLE, "") if remove else text.rstrip("\n") + ("\n\n" if text else "") + CODEX_TABLE
    if tomllib.loads(new).get("mcp_servers", {}).get("whatsapp") != (None if remove else ENTRY):
        raise RuntimeError(f"{CODEX} has an unusual layout; edit its [mcp_servers.whatsapp] table by hand.")
    _save(CODEX, new)
    return (f"Removed from {CODEX}." if remove else
            f"Registered in {CODEX} (ChatGPT for Mac, Codex CLI and IDE extension). Restart the app.")


def show() -> str:
    return "Local MCP server (stdio) for any client:\n" + json.dumps({"whatsapp": ENTRY}, indent=2)


def remove() -> str:
    """Undo all three registrations, each only if it is this repo's."""
    messages = []
    for client in (claude_code, claude_desktop, codex):
        try:
            messages.append(client(remove=True))
        except (OSError, ValueError, RuntimeError) as exc:
            messages.append(f"Could not unregister: {exc}")
    return "\n".join(messages)


def main() -> int:
    targets = {"claude-code": claude_code, "claude-desktop": claude_desktop, "codex": codex,
               "show": show, "remove": remove}
    target = sys.argv[1] if len(sys.argv) == 2 else ""
    if target not in targets:
        print(f"Usage: register.py {' | '.join(targets)}")
        return 2
    try:
        print(targets[target]())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Could not register: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
