"""scripts/register.py against a temporary HOME and a fake `claude`: real clients are never touched."""

import importlib.util
import json
import subprocess
import sys
import tomllib

import pytest
from conftest import REPO

FAKE_CLAUDE = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/calls"
if [ "$2" = get ]; then cat "$(dirname "$0")/existing" 2>/dev/null || exit 1; fi
"""


@pytest.fixture
def reg(tmp_path, monkeypatch):
    home, bin_dir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    spec = importlib.util.spec_from_file_location("register", REPO / "scripts" / "register.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert str(module.DESKTOP).startswith(str(home)) and str(module.CODEX).startswith(str(home))
    monkeypatch.setattr(module, "desktop_running", lambda: False)
    monkeypatch.setattr(module, "wait_detached", lambda: "waiting for Cmd+Q")
    module.bin_dir = bin_dir
    return module


def run_main(reg, monkeypatch, capsys, target):
    monkeypatch.setattr(sys, "argv", ["register.py", target])
    code = reg.main()
    return code, capsys.readouterr().out


def mode(path):
    return oct(path.stat().st_mode & 0o777)


def test_codex_add_keeps_the_file_and_one_backup(reg):
    reg.CODEX.parent.mkdir(parents=True)
    reg.CODEX.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "o"\n')
    assert "Registered" in reg.codex()
    assert "already has this server" in reg.codex()
    data = tomllib.loads(reg.CODEX.read_text())
    assert data["model"] == "x" and data["mcp_servers"]["whatsapp"] == reg.ENTRY and "other" in data["mcp_servers"]
    backup = reg.CODEX.with_name("config.toml.bak-whatsapp")
    assert mode(reg.CODEX) == mode(backup) == "0o600" and "whatsapp" not in backup.read_text()
    assert "Removed" in reg.codex(remove=True)
    assert tomllib.loads(reg.CODEX.read_text()) == {"model": "x", "mcp_servers": {"other": {"command": "o"}}}
    assert "whatsapp" not in backup.read_text()  # the first backup is kept


def test_codex_leaves_foreign_entries_and_invalid_files_alone(reg, monkeypatch, capsys):
    reg.CODEX.parent.mkdir(parents=True)
    foreign = '[mcp_servers.whatsapp]\ncommand = "/elsewhere/python"\n'
    reg.CODEX.write_text(foreign)
    assert "not this repo's" in reg.codex() and "not this repo's" in reg.codex(remove=True)
    reg.CODEX.write_text("[broken\n")
    assert run_main(reg, monkeypatch, capsys, "codex")[0] == 1
    assert reg.CODEX.read_text() == "[broken\n"


def test_desktop_depends_on_whether_the_app_is_open(reg, monkeypatch, capsys):
    assert "Registered in Claude Desktop" in reg.claude_desktop()
    assert json.loads(reg.DESKTOP.read_text())["mcpServers"]["whatsapp"] == reg.ENTRY
    assert mode(reg.DESKTOP) == "0o600"
    monkeypatch.setattr(reg, "desktop_running", lambda: True)
    code, out = run_main(reg, monkeypatch, capsys, "claude-desktop")
    assert code == 1 and "quit it with Cmd+Q" in out
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    assert reg.claude_desktop() == "waiting for Cmd+Q"


def test_desktop_remove_keeps_other_servers(reg):
    reg.DESKTOP.parent.mkdir(parents=True)
    reg.DESKTOP.write_text(json.dumps({"mcpServers": {"other": {"command": "o"}}}))
    reg.write_desktop()
    reg.write_desktop(remove=True)
    assert json.loads(reg.DESKTOP.read_text()) == {"mcpServers": {"other": {"command": "o"}}}


def fake_claude(reg, existing=None):
    path = reg.bin_dir / "claude"
    path.write_text(FAKE_CLAUDE)
    path.chmod(0o755)
    if existing:
        (reg.bin_dir / "existing").write_text(f"whatsapp:\n  Command: {existing}\n")
    return reg.bin_dir / "calls"


def test_claude_code_replaces_only_its_own_entry(reg):
    calls = fake_claude(reg, existing="/elsewhere/python")
    assert "not this repo's" in reg.claude_code() and "not this repo's" in reg.claude_code(remove=True)
    assert "add" not in calls.read_text() and "remove" not in calls.read_text()
    (reg.bin_dir / "existing").write_text(f"whatsapp:\n  Command: {reg.COMMAND}\n")
    assert "Registered" in reg.claude_code()
    assert calls.read_text().splitlines()[-2:] == [
        "mcp remove --scope user whatsapp", f"mcp add --scope user whatsapp -- {reg.COMMAND} {' '.join(reg.ARGS)}"]


def test_remove_without_clients_is_quiet(reg, monkeypatch, capsys):
    code, out = run_main(reg, monkeypatch, capsys, "remove")
    assert code == 0 and "not on the PATH" in out and "nothing to remove" in out


def test_install_never_deletes_a_path_not_ending_in_waexport(tmp_path, helper_binary):
    keep = tmp_path / "Applications" / "keep"
    keep.parent.mkdir()
    keep.write_text("x")
    proc = subprocess.run(["make", "install", f"INSTALLED={keep.parent}"], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode != 0 and keep.exists() and "must end in /WAExport.app" in proc.stderr
