"""scripts/register.py against a temporary HOME and a fake `claude`: real clients are never touched."""

import contextlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
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


# --- Claude Desktop waiter -------------------------------------------------------------------

WAITER = """
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("register", sys.argv[1])
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)
pid_file = Path(sys.argv[2])
reg.WAIT_SECONDS = 60


def claude_is_open():
    tmp = pid_file.with_suffix(".tmp")
    tmp.write_text(str(os.getpid()))
    os.replace(tmp, pid_file)
    return True


reg.desktop_running = claude_is_open
print(os.getpid(), reg.claude_desktop())
"""


def test_desktop_waiter_releases_the_callers_output(tmp_path):
    pid_file = tmp_path / "waiter.pid"
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"}
    try:
        proc = subprocess.run([sys.executable, "-c", WAITER, str(REPO / "scripts" / "register.py"), str(pid_file)],
                              capture_output=True, text=True, env=env, timeout=15)
        assert "Quit Claude Desktop" in proc.stdout
        for _ in range(50):  # the caller wrote its pid; the waiter overwrites it on its first check
            if pid_file.read_text() != proc.stdout.split()[0]:
                break
            time.sleep(0.1)
        os.kill(int(pid_file.read_text()), 0)  # still waiting, yet the caller already got EOF
    finally:
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)


# --- Codex removal ---------------------------------------------------------------------------

OTHER = '[mcp_servers.other]\ncommand = "o"\n'


def codex_file(reg, text):
    reg.CODEX.parent.mkdir(parents=True, exist_ok=True)
    reg.CODEX.write_text(text)


def test_codex_remove_handles_reordered_and_requoted_tables(reg):
    codex_file(reg, f"# mine\n{OTHER}\n[ mcp_servers . \"whatsapp\" ]  # added by hand\n"
                    f"args = {json.dumps(reg.ARGS)}\ncommand = '{reg.COMMAND}'\n\n"
                    "[mcp_servers.whatsapp.env]\nX = \"1\"\n\n[profile]\nmodel = \"m\"\n")
    assert "Removed" in reg.codex(remove=True)
    text = reg.CODEX.read_text()
    assert text.startswith("# mine\n") and "whatsapp" not in text
    assert tomllib.loads(text) == {"mcp_servers": {"other": {"command": "o"}}, "profile": {"model": "m"}}


def test_codex_remove_takes_extra_keys_with_the_table(reg):
    codex_file(reg, OTHER + "\n" + reg.CODEX_TABLE + "enabled = false\n")
    assert "Removed" in reg.codex(remove=True)
    assert tomllib.loads(reg.CODEX.read_text()) == {"mcp_servers": {"other": {"command": "o"}}}


def test_remove_fails_and_keeps_the_file_when_codex_cannot_be_edited_safely(reg, monkeypatch, capsys):
    inline = f'[mcp_servers]\nwhatsapp = {{ command = "{reg.COMMAND}", args = {json.dumps(reg.ARGS)} }}\n'
    codex_file(reg, inline)
    code, out = run_main(reg, monkeypatch, capsys, "remove")
    assert code == 1 and "Could not unregister" in out and "edit its [mcp_servers.whatsapp] table by hand" in out
    assert reg.CODEX.read_text() == inline


def test_remove_fails_when_claude_code_keeps_the_entry(reg, monkeypatch, capsys):
    fake_claude(reg, existing=reg.COMMAND)
    script = reg.bin_dir / "claude"
    script.write_text(script.read_text() + 'if [ "$2" = remove ]; then exit 1; fi\n')
    code, out = run_main(reg, monkeypatch, capsys, "remove")
    assert code == 1 and "`claude mcp remove` failed" in out


# --- setup.sh, with every external command faked ----------------------------------------------

FAKES = {
    "sw_vers": 'echo "${FAKE_MACOS:-15.1}"',
    "mdfind": "echo /Applications/WhatsApp.app",
    "xcode-select": "echo /Library/Developer/CommandLineTools",
    "swiftc": "exit 0",
    "open": 'echo "open $*" >> "$FAKES/calls"\n'
            'if [ "$1" = -R ] && [ -n "${DRAG_TO:-}" ]; then\n'
            '  chmod u+w "$(dirname "$DRAG_TO")"; cp -R "$2" "$DRAG_TO"\nfi',
    "make": 'echo "make $*" >> "$FAKES/calls"\nbin=build/WAExport.app/Contents/MacOS/WAExport\n'
            'mkdir -p "$(dirname "$bin")"; echo bin > "$bin"; chmod +x "$bin"\n'
            'case "$*" in *install*) dest="${3#INSTALLED=}"\n'
            '  mkdir -p "$dest" && cp -R build/WAExport.app/. "$dest" ;; esac',
    "uv": 'case "$*" in *"wa refresh"*) cat "$FAKES/refresh_out"; exit "$(cat "$FAKES/refresh_code")" ;; esac',
}


@pytest.fixture
def setup_sh(tmp_path):
    """A copy of the repo's scripts/setup.sh in a scratch repo, so the fake build never touches build/."""
    repo, fakes, home, apps = tmp_path / "repo", tmp_path / "fakes", tmp_path / "home", tmp_path / "Apps"
    (repo / "scripts").mkdir(parents=True)
    (repo / "config").mkdir()
    shutil.copy(REPO / "scripts" / "setup.sh", repo / "scripts" / "setup.sh")
    (repo / "config" / "policy.example.toml").write_text("")
    (home / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared").mkdir(parents=True)
    fakes.mkdir()
    apps.mkdir()
    for name, body in FAKES.items():
        (fakes / name).write_text(f"#!/bin/sh\n{body}\n")
        (fakes / name).chmod(0o755)
    (fakes / "calls").write_text("")
    (fakes / "refresh_out").write_text("WhatsApp data as of today.\n")
    (fakes / "refresh_code").write_text("0")
    env = {"HOME": str(home), "PATH": f"{fakes}:/usr/bin:/bin", "FAKES": str(fakes),
           "INSTALLED": str(apps / "WAExport.app")}

    def run(stdin: str = "", **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/bash", str(repo / "scripts" / "setup.sh")], input=stdin, capture_output=True,
                              text=True, env=env | extra, timeout=60)

    run.fakes, run.apps = fakes, apps
    yield run
    apps.chmod(0o755)


def test_setup_script_is_valid_bash():
    assert subprocess.run(["/bin/bash", "-n", str(REPO / "scripts" / "setup.sh")]).returncode == 0


def test_setup_stops_on_old_macos(setup_sh):
    proc = setup_sh(FAKE_MACOS="13.6.1")
    assert proc.returncode == 1 and "macOS 14" in proc.stderr and "13.6.1" in proc.stderr


def test_setup_installs_with_make_when_applications_is_writable(setup_sh):
    proc = setup_sh()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "make -s install" in (setup_sh.fakes / "calls").read_text()
    assert (setup_sh.apps / "WAExport.app" / "Contents" / "MacOS" / "WAExport").exists()


def test_setup_asks_for_a_finder_copy_when_applications_is_not_writable(setup_sh):
    setup_sh.apps.chmod(0o555)
    target = setup_sh.apps / "WAExport.app"
    proc = setup_sh(stdin="\n", DRAG_TO=str(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = (setup_sh.fakes / "calls").read_text()
    assert "install" not in calls and "open -R build/WAExport.app" in calls and "drag" in proc.stdout
    setup_sh.apps.chmod(0o555)
    (target / "Contents" / "MacOS" / "WAExport").write_text("old build\n")
    proc = setup_sh(stdin="\n")
    assert proc.returncode == 1 and "build/WAExport.app" in proc.stderr


def test_setup_shows_warnings_from_a_successful_refresh(setup_sh):
    (setup_sh.fakes / "refresh_out").write_text("WhatsApp data as of today.\nWARNING: the canary fired.\n")
    proc = setup_sh()
    assert proc.returncode == 0 and "WARNING: the canary fired." in proc.stdout


@pytest.mark.parametrize("reason, denied", [
    ("Could not refresh: the policy is invalid.", False),
    ("Could not refresh: WAExport has no Full Disk Access (System Settings ...).", True)])
@pytest.mark.parametrize("warning_first", [True, False])
def test_setup_classifies_refresh_failures_by_the_reason_line(setup_sh, reason, denied, warning_first):
    warning = "WARNING: this terminal has Full Disk Access."
    (setup_sh.fakes / "refresh_out").write_text(
        "\n".join([warning, reason] if warning_first else [reason, "chat notes", warning]) + "\n")
    (setup_sh.fakes / "refresh_code").write_text("1")
    proc = setup_sh()
    assert proc.returncode == 1
    assert ("Privacy_AllFiles" in (setup_sh.fakes / "calls").read_text()) == denied
    assert ("other than Full Disk Access" in proc.stderr) != denied
