"""Runs WAExport.app (the only process with Full Disk Access) and keeps the export fresh.

WAExport is launched through LaunchServices (`open`) so macOS attributes the
access to WAExport itself, not to Claude or the terminal that asked for it.
"""

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import paths, transcripts
from .policy import load_policy, resolve
from .store import Store, check_schema, connect, exported_at, latest_message
from .text import LOCAL, fmt, plural, to_coredata

STALE_AFTER_MINUTES = 15
CANARY_WARNING = ("WARNING: the process running this tool (your AI app, `claude` or your terminal) has Full "
                  "Disk Access and could read WhatsApp unfiltered. Only WAExport should have it: remove the "
                  "others in System Settings > Privacy & Security > Full Disk Access.")


class ExportError(RuntimeError):
    pass


def _prepare_data_dir() -> None:
    """Apply WAExport's rule for its arguments (the default location or one under the temp dir) before
    creating anything, so a refusal reads as an explanation instead of a helper that never answered."""
    def canonical(p: Path) -> Path:
        p = Path(os.path.normpath(p.expanduser().absolute()))
        return p.parent.resolve() / p.name
    temp = Path(tempfile.gettempdir()).resolve()
    for variable, default in (("WA_DATA_DIR", paths.DEFAULT_DATA_DIR), ("WA_SOURCE_DIR", paths.WHATSAPP_DB.parent)):
        real = canonical(Path(os.environ.get(variable) or default))
        if real != canonical(default) and temp not in real.parents:
            raise ExportError(f"{variable} must be {default} or a folder under {temp}, not {os.environ[variable]}.")
    data = paths.data_dir()
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    if data.is_symlink() or data.lstat().st_uid != os.getuid():
        raise ExportError(f"{data} must be a folder of your own, not a link or another user's folder.")
    if data.lstat().st_mode & 0o077:
        data.chmod(0o700)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_stamp(value: object) -> datetime | None:
    """An aware ISO 8601 time, or None for anything else (a damaged status must not crash a view)."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _policy_digest() -> str:
    """Hash of both policy files' bytes ('' when one cannot be read: then nothing counts as fresh)."""
    digest = hashlib.sha256()
    for file in (paths.policy_file(), paths.claude_file(paths.policy_file())):
        try:
            digest.update(hashlib.sha256(file.read_bytes() if file.exists() else b"").digest())
        except OSError:
            return ""
    return digest.hexdigest()


def _fingerprint(policy_digest: str, jids: object) -> str:
    return hashlib.sha256(f"{policy_digest}{json.dumps(jids)}".encode()).hexdigest() if policy_digest else ""


def run_helper(verb: str = "export") -> dict:
    """Run WAExport once and return its status file (raises if the helper never answered)."""
    run_id = uuid.uuid4().hex
    _prepare_data_dir()
    status_file = paths.data_dir() / "audio.json" if verb == "audio" else paths.status_file()
    args = [verb, "--run", run_id, "--data", str(paths.data_dir())]
    if os.environ.get("WA_SOURCE_DIR"):
        args += ["--source", os.environ["WA_SOURCE_DIR"]]
    if os.environ.get("WA_HELPER_BIN"):  # tests run the binary directly
        command = [os.environ["WA_HELPER_BIN"], *args]
    elif paths.helper_app().exists():
        command = ["/usr/bin/open", "-W", "-g", "-n", str(paths.helper_app()), "--args", *args]
    else:
        raise ExportError(f"{paths.helper_app()} not found: run make install (or make setup).")
    proc = subprocess.run(command, timeout=180, capture_output=True, text=True, check=False)
    status = _read_json(status_file)
    if status.get("run") != run_id:
        detail = (proc.stderr or "").strip()[:300]
        if "-10825" in detail:  # LaunchServices: the app needs a newer macOS than this one
            raise ExportError(f"WAExport was built for a newer macOS (error -10825): run `make setup` in {paths.REPO}.")
        raise ExportError(f"WAExport did not answer (exit code {proc.returncode}). {detail}".strip())
    return status


def _export() -> dict:
    digest = _policy_digest()  # before loading: an edit made during the export then reads as stale
    policy = load_policy(paths.policy_file())
    since = to_coredata(datetime.now(UTC) - timedelta(days=policy.days))
    previous = _read_json(paths.selection_file()).get("jids", [])
    # Move the cutoff on every export; the chats can only be resolved against the fresh index.
    paths.write_private(paths.selection_file(), json.dumps({"jids": previous, "since": since}, indent=1))
    status = run_helper()
    if not status.get("ok"):
        return status
    with connect() as conn:
        sel = resolve(conn, policy)
    wanted = {"jids": sorted(sel.content), "since": since}
    if previous != wanted["jids"]:
        paths.write_private(paths.selection_file(), json.dumps(wanted, indent=1))
        status = run_helper()
    with connect() as conn:
        status = _with_schema(status, conn)
    if status.get("ok"):  # only a complete export under this policy and selection counts as fresh
        status["fingerprint"] = _fingerprint(digest, wanted["jids"])
        paths.write_private(paths.status_file(), json.dumps(status, indent=1))
        with connect() as conn:
            transcripts.prune(conn)  # revoked chats and messages outside the window lose their transcripts
    return status


def copy_audio(pks: list[int]) -> dict:
    """Have WAExport copy these voice notes into data/audio; it re-checks each against the selection."""
    with _lock():
        paths.write_private(paths.data_dir() / "audio-request.json", json.dumps({"pks": pks}))
        try:
            return run_helper("audio")
        finally:
            (paths.data_dir() / "audio-request.json").unlink(missing_ok=True)


def _with_schema(status: dict, conn: sqlite3.Connection) -> dict:
    """WAExport copies tables whole: a column the Python side needs may still be gone."""
    missing = check_schema(conn)
    if not missing:
        return status
    return {**status, "ok": False, "error_kind": "schema", "error": "missing " + ", ".join(missing)}


def _is_fresh(status: dict) -> bool:
    stamp = _parse_stamp(status.get("exported_at")) if status.get("ok") else None
    if not stamp or not paths.export_db().exists():
        return False
    if datetime.now(UTC) - stamp > timedelta(minutes=STALE_AFTER_MINUTES):
        return False
    # Made under today's policy files, with the selection the last complete export applied.
    expected = _fingerprint(_policy_digest(), _read_json(paths.selection_file()).get("jids"))
    return bool(expected) and status.get("fingerprint") == expected


@contextmanager
def _lock() -> Iterator[None]:
    """Serialises exports across threads and across the MCP processes of several clients."""
    _prepare_data_dir()
    with os.fdopen(os.open(paths.data_dir() / "refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes
        yield


def refresh(only_if_stale: bool = False) -> dict:
    """Export, apply the policy to the fresh index and re-export if the selection changed.

    Never raises for an expected failure: it returns a status with `error_kind` and keeps the
    previous export (if any)."""
    try:
        with _lock():
            status = _read_json(paths.status_file())
            if only_if_stale and _is_fresh(status):  # another process may have refreshed while we waited
                return status
            return _export()
    except ValueError as exc:
        return {"ok": False, "error_kind": "policy", "error": str(exc)}
    except (FileNotFoundError, sqlite3.Error) as exc:  # the helper said ok but its export is unusable
        return {"ok": False, "error_kind": "helper", "error": f"the export is unreadable: {exc}"}
    except (ExportError, subprocess.TimeoutExpired, OSError) as exc:  # OSError: e.g. an unreadable policy file
        return {"ok": False, "error_kind": "helper", "error": str(exc)}


def ensure_fresh() -> dict:
    """Refresh when the export is missing, stale, failed or made under other policy files."""
    status = _read_json(paths.status_file())
    return status if _is_fresh(status) else refresh(only_if_stale=True)


def caller_can_read_whatsapp(db: Path = paths.WHATSAPP_DB) -> bool:
    """Canary: True means this process (Claude or its terminal) has Full Disk Access. It should not."""
    try:
        with open(db, "rb"):
            return True
    except OSError:
        return False


def describe(status: dict, conn: sqlite3.Connection | None = None) -> str:
    """One line on data freshness for the top of every answer."""
    moment = _parse_stamp(status.get("exported_at") or (exported_at(conn) if conn else None))
    when = fmt(moment.astimezone(LOCAL)) if moment else "never"
    if status.get("ok"):
        latest = latest_message(conn) if conn else None
        line = f"WhatsApp data as of {when}"
        if latest:
            line += f" · latest message {fmt(latest)}"
            if datetime.now(LOCAL) - latest > timedelta(hours=24):
                line += " (is WhatsApp open on this Mac?)"
        return line + "."
    error = status.get("error", "unknown error")
    hint = {
        "fda_denied": f"WAExport has no Full Disk Access: run `make grant` in {paths.REPO}",
        "source_missing": f"WhatsApp for Mac data not found: {error}",
        "schema": f"WhatsApp changed its database: {error}; store.py needs adapting",
    }.get(status.get("error_kind", ""), error)
    return f"Could not refresh: {hint}. Latest data: {when}."


def report(status: dict) -> tuple[bool, str]:
    """What `wa status` and whatsapp_refresh show: freshness, counts and policy notes."""
    try:
        with connect() as conn:
            status = _with_schema(status, conn)
            lines = [describe(status, conn)]
            if status.get("error_kind") not in ("policy", "schema"):
                lines += Store(conn).policy_notes()
    except FileNotFoundError:
        lines = [describe(status)]
    if status.get("ok"):
        lines.insert(1, f"{plural(status.get('chats', 0), 'chat')} in the index · "
                        f"{plural(status.get('messages', 0), 'message')} from "
                        f"{plural(status.get('selected', 0), 'content chat')} · SQLite {status.get('sqlite_version')}")
    return bool(status.get("ok")), "\n".join(lines)


def with_canary(text: str, exposed: bool | None = None) -> str:
    """Every CLI and MCP answer goes through here: the Full Disk Access warning comes first."""
    exposed = caller_can_read_whatsapp() if exposed is None else exposed
    return f"{CANARY_WARNING}\n{text}" if exposed else text


TRANSCRIBE_MAX_SECONDS = 15 * 60
TRANSCRIBE_OFF = ('Transcription is off: set [options] transcribe = "es-ES" (your language) in '
                  "config/policy.toml.")


def transcriber() -> Path:
    return Path(os.environ.get("WA_TRANSCRIBER") or paths.REPO / "build" / "wa-transcribe")


def _transcriber_lines(args: list[str], timeout: float) -> list[dict]:
    if not transcriber().exists():
        raise ExportError(f"{transcriber()} not found: run `make build` in {paths.REPO}.")
    proc = subprocess.run([str(transcriber()), *args], capture_output=True, text=True, timeout=timeout, check=False)
    items = []
    for line in proc.stdout.splitlines():
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    for item in items:
        if "error" in item and "file" not in item:
            raise ExportError(f"Transcription: {item['error']}")
    return items


def install_language() -> str:
    """Ask macOS to download the speech model for the policy's language (from Apple)."""
    locale = load_policy(paths.policy_file()).transcribe
    if not locale:
        raise ValueError(TRANSCRIBE_OFF)
    _transcriber_lines(["--install", locale], timeout=1800)
    return f"Speech model for {locale} is installed."


def transcribe(chat: str, since: datetime | None = None, until: datetime | None = None, limit: int = 20) -> str:
    """Transcribe the voice notes of one allowed chat on this Mac (Apple's on-device engine).

    WAExport copies only the notes still missing a transcript; the copies are deleted afterwards and only
    the text is kept. Returns a summary followed by the chat with its transcripts."""
    status = ensure_fresh()
    if not status.get("ok"):
        raise ExportError(f"Could not refresh: {status.get('error', 'unknown error')}")
    with connect() as conn:
        store = Store(conn)
        locale = store.policy.transcribe
        if not locale:
            raise ValueError(TRANSCRIBE_OFF)
        notes = store.voice_notes(chat, since, until, limit)
    cached = transcripts.load()
    missing = [n for n in notes if not n["media_path"]]
    too_long = [n for n in notes if n["media_path"] and (n["media_seconds"] or 0) > TRANSCRIBE_MAX_SECONDS]
    todo = [n for n in notes if n["Z_PK"] not in cached and n["media_path"] and n not in too_long]
    done, failed, not_downloaded, audio = [], 0, len(missing), paths.data_dir() / "audio"
    if todo:
        try:
            _empty(audio)  # leftovers of an interrupted run
            copy = copy_audio([n["Z_PK"] for n in todo])
            if not copy.get("ok"):
                raise ExportError(f"WAExport could not copy the voice notes: {copy.get('error', 'unknown error')}")
            files = {c["file"]: c["pk"] for c in copy.get("copied", [])}
            gone = sum(s.get("reason") == "not downloaded" for s in copy.get("skipped", []))  # path but no file
            not_downloaded += gone
            failed += len(todo) - len(files) - gone
            if files:
                seconds = sum(n["media_seconds"] or 0 for n in todo)
                items = _transcriber_lines([locale, *(str(audio / f) for f in files)], timeout=60 + seconds / 5)
                for item in items:
                    if item.get("file") in files and "text" in item:
                        done.append({**item, "pk": files[item["file"]]})
                failed += len(files) - len(done)
        finally:
            _empty(audio)
        transcripts.save(done)
    lines = [f"Transcribed {plural(len(done), 'voice note')} on this Mac ({locale}); "
             f"{len(notes) - len(todo) - len(missing) - len(too_long)} already had a transcript."]
    if not_downloaded:
        lines.append(f"{plural(not_downloaded, 'voice note')} not downloaded on this Mac: play them once in WhatsApp.")
    if too_long:
        lines.append(f"{plural(len(too_long), 'voice note')} longer than {TRANSCRIBE_MAX_SECONDS // 60} min skipped.")
    if failed:
        lines.append(f"{plural(failed, 'voice note')} could not be transcribed.")
    with connect() as conn:
        return "\n".join(lines) + "\n\n" + Store(conn).messages(chat, since, until)


def _empty(folder: Path) -> None:
    """Delete the copied audio files (only regular files; the folder itself stays, private)."""
    if folder.is_dir() and not folder.is_symlink():
        for item in folder.iterdir():
            if item.is_file() and not item.is_symlink():
                item.unlink(missing_ok=True)
