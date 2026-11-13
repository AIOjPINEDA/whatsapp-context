"""Locations, read on each call so environment overrides (used by tests) apply, and private writes."""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WHATSAPP_DB = Path("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite").expanduser()
DEFAULT_DATA_DIR = Path("~/.local/share/whatsapp-context").expanduser()


def data_dir() -> Path:
    return Path(os.environ.get("WA_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()


def policy_file() -> Path:
    return Path(os.environ.get("WA_POLICY", REPO / "config" / "policy.toml")).expanduser()


def claude_file(policy: Path) -> Path:
    """Changes made from a chat (whatsapp_allow / revoke / exclude) live next to the policy."""
    return policy.with_name("policy-claude.toml")


def helper_app() -> Path:
    return Path(os.environ.get("WA_HELPER_APP", "/Applications/WAExport.app"))


def export_db() -> Path:
    return data_dir() / "wa.sqlite"


def status_file() -> Path:
    return data_dir() / "status.json"


def selection_file() -> Path:
    return data_dir() / "selection.json"


def write_private(path: Path, text: str) -> None:
    """Replace `path` atomically with a file only its owner can read, 0600 from the moment it exists."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)  # O_EXCL below then never reuses a stale file or follows a planted link
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)
    tmp.replace(path)
