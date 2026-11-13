"""Changes to the policy requested from a chat. They go to config/policy-claude.toml, never to
config/policy.toml, so the user's own file and its exclusions stay as written."""

import fcntl
import json
import os
import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path

from . import paths
from .policy import (
    PROJECT_KEYS,
    SECTION_KEYS,
    Identities,
    Selection,
    chat_index,
    load_policy,
    match,
    parse_policy,
    read_toml,
    resolve,
)

HEADER = ("# Managed from the chat with whatsapp_allow / whatsapp_revoke / whatsapp_exclude.\n"
          "# You may edit it or move its entries into policy.toml. Exclusions in policy.toml always win.\n"
          "# Chats are kept by JID, with the name as a comment. Rewrites keep every key, not your comments.\n")


def _q(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings, integers and string arrays are valid TOML


def _value(value: object, names: dict[str, str]) -> str:
    if not isinstance(value, list) or not any(v in names for v in value):
        return _q(value)
    # Comments end at the line break, so a name is flattened and stripped of control characters.
    lines = [f"  {_q(v)},  # {''.join(c for c in ' '.join(names[v].split()) if c.isprintable())}"
             if v in names else f"  {_q(v)}," for v in value]
    return "[\n" + "\n".join(lines) + "\n]"


def _dump(data: dict, names: dict[str, str]) -> str:
    out = [HEADER]
    for section in ("content", "exclude", "options"):
        body = "".join(f"{key} = {_value(data[section][key], names)}\n"
                       for key in sorted(SECTION_KEYS[section]) if key in data.get(section, {}))
        if body:
            out.append(f"[{section}]\n{body}")
    for name, project in sorted(data.get("projects", {}).items()):
        body = "".join(f"{key} = {_value(project[key], names)}\n" for key in sorted(PROJECT_KEYS) if key in project)
        out.append(f"[projects.{_q(name)}]\n{body}")
    return "\n".join(out)


def _target() -> Path:
    return paths.claude_file(paths.policy_file())


def _locked(edit: Callable[..., str]) -> Callable[..., str]:
    """Serialise every read-validate-modify-write across processes, so no edit undoes another."""
    @wraps(edit)
    def run(*args: object, **kwargs: object) -> str:
        lock = _target().with_name(_target().name + ".lock")
        with os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes
            return edit(*args, **kwargs)
    return run


def _save(conn: sqlite3.Connection, data: dict) -> None:
    parse_policy(data, _target())  # never write something load_policy would reject
    paths.write_private(_target(), _dump(data, {jid: name for _, jid, name in chat_index(conn) if name}))


def _effective(conn: sqlite3.Connection) -> Selection:
    return resolve(conn, load_policy(paths.policy_file()))


def _resolve_one(conn: sqlite3.Connection, chat: str) -> tuple[str, set[str]]:
    """Exactly one non-excluded chat (or one linked phone/@lid person) -> (name, JIDs to persist).

    Excluded chats answer like missing ones, so the assistant cannot confirm they exist."""
    excluded = _effective(conn).excluded
    chats = [c for c in chat_index(conn) if c[1] not in excluded]
    ids = Identities(conn)
    found, ambiguous = match(chat, chats, ids)
    if ambiguous:
        raise ValueError(f'"{chat}" matches several chats; use its phone number or JID.')
    if not found:
        raise ValueError(f'No chat is named exactly "{chat}". '
                         "Copy the name exactly as the inbox shows it.")
    name = next(n for _, j, n in sorted(chats) if j in found)
    # The JIDs, never the name: a name can be renamed, shared, or look like a JID or a number.
    return (name or chat).strip(), set().union(*(ids.aliases(j) for j in found))


@_locked
def allow(conn: sqlite3.Connection, chat: str, project: str, context: str | None = None) -> str:
    """Grant content access inside one project. A new project needs its context (folder or file)."""
    own = load_policy(paths.policy_file())
    name, jids = _resolve_one(conn, chat)
    current = resolve(conn, own)
    projects = {p.name for p in own.projects}
    if project not in projects and not context:
        raise ValueError(f'There is no project named "{project}". Use one of these, or pass `context` (a folder '
                         f"or file) to create it: {', '.join(sorted(projects)) or '(none yet)'}.")
    already = {current.project_of[j] for j in jids if j in current.project_of} - {project}
    if already:
        raise ValueError(f'"{name}" already belongs to "{already.pop()}". Moving it to another project is done '
                         "by hand in config/policy.toml.")
    data = read_toml(_target())
    target = data.setdefault("projects", {}).setdefault(project, {})
    if context and project not in projects:
        target["context"] = context
    chats = target.setdefault("chats", [])
    chats += [j for j in sorted(jids) if j not in chats]
    _save(conn, data)
    return f'"{name}" now has content access in project "{project}".'


def _still_granted(conn: sqlite3.Connection, jids: set[str]) -> str | None:
    sel = _effective(conn)
    jid = next((j for j in sorted(jids) if j in sel.content), None)
    if jid is None:
        return None
    if jid in sel.via_pattern:
        return f'the pattern "{sel.via_pattern[jid]}"'
    if jid in sel.via_people:
        return f'a people rule ("{sel.via_people[jid]}")'
    return "a chat entry in config/policy.toml"


@_locked
def revoke(conn: sqlite3.Connection, chat: str) -> str:
    name, jids = _resolve_one(conn, chat)
    data = read_toml(_target())
    removed = False
    chats, ids = chat_index(conn), Identities(conn)
    for section in [data.get("content", {}), *data.get("projects", {}).values()]:
        if "chats" in section:
            keep = [c for c in section["chats"] if not jids & match(c, chats, ids)[0]]
            removed |= len(keep) != len(section["chats"])
            section["chats"] = keep
    if removed:
        _save(conn, data)
    reason = _still_granted(conn, jids)
    if reason:
        return (f'"{name}" still has content access through {reason}. Edit that rule by hand, or use '
                "whatsapp_exclude to hide the chat.")
    return f'"{name}" no longer has content access.' if removed else f'"{name}" did not have content access.'


@_locked
def exclude(conn: sqlite3.Connection, chat: str) -> str:
    name, jids = _resolve_one(conn, chat)
    data = read_toml(_target())
    chats = data.setdefault("exclude", {}).setdefault("chats", [])
    chats += [j for j in sorted(jids) if j not in chats]
    _save(conn, data)
    if not ({j for _, j, _ in chat_index(conn)} & jids) <= _effective(conn).excluded:
        raise ValueError(f'"{name}" could not be excluded; check config/policy-claude.toml.')
    return f'"{name}" is now excluded: it will not appear even in the inbox.'
