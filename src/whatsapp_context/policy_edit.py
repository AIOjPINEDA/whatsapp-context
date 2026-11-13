"""Changes to the policy requested from a chat. They go to config/policy-claude.toml, never to
config/policy.toml, so the user's own file and its exclusions stay as written."""

import json
import sqlite3
from pathlib import Path

from . import paths
from .policy import Identities, chat_index, load_policy, match, parse_policy, read_toml, resolve

HEADER = ("# Managed from the chat with whatsapp_allow / whatsapp_revoke / whatsapp_exclude.\n"
          "# You may edit it or move its entries into policy.toml. Exclusions in policy.toml always win.\n"
          "# Only chats, people and patterns (with a project's context and repo) are kept when it is rewritten.\n")


def _q(value: str | list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings and string arrays are valid TOML


def _dump(data: dict) -> str:
    out = [HEADER]
    for section, keys in (("content", ("chats", "people")), ("exclude", ("chats",))):
        body = "".join(f"{key} = {_q(data[section][key])}\n" for key in keys if data.get(section, {}).get(key))
        if body:
            out.append(f"[{section}]\n{body}")
    for name, project in sorted(data.get("projects", {}).items()):
        # Keep every key the policy understands: a project edited by hand must survive a whatsapp_allow.
        lists = [(key, project[key]) for key in ("chats", "people", "patterns") if project.get(key)]
        if lists:
            body = "".join(f"{key} = {_q(project[key])}\n" for key in ("context", "repo") if project.get(key))
            body += "".join(f"{key} = {_q(values)}\n" for key, values in lists)
            out.append(f"[projects.{_q(name)}]\n{body}")
    return "\n".join(out)


def _target() -> Path:
    return paths.claude_file(paths.policy_file())


def _save(data: dict) -> None:
    parse_policy(data, _target())  # never write something load_policy would reject
    paths.write_private(_target(), _dump(data))


def _resolve_one(conn: sqlite3.Connection, chat: str) -> tuple[str, str]:
    """Exactly one non-excluded chat by exact name, phone or JID -> (name, jid).

    Excluded chats answer like missing ones, so the assistant cannot confirm they exist."""
    excluded = resolve(conn, load_policy(paths.policy_file())).excluded
    chats = [c for c in chat_index(conn) if c[1] not in excluded]
    found, ambiguous = match(chat, chats, Identities(conn))
    if ambiguous or len(found) > 1:
        raise ValueError(f'"{chat}" matches several chats; use its phone number or JID.')
    if not found:
        raise ValueError(f'No chat is named exactly "{chat}". '
                         "Copy the name exactly as the inbox shows it.")
    jid = found.pop()
    name = next(n for _, j, n in chats if j == jid)
    return (name or chat).strip(), jid


def allow(conn: sqlite3.Connection, chat: str, project: str, context: str | None = None) -> str:
    """Grant content access inside one project. A new project needs its context (folder or file)."""
    own = load_policy(paths.policy_file())
    name, jid = _resolve_one(conn, chat)
    current = resolve(conn, own)
    projects = {p.name for p in own.projects}
    if project not in projects and not context:
        raise ValueError(f'There is no project named "{project}". Use one of these, or pass `context` (a folder '
                         f"or file) to create it: {', '.join(sorted(projects)) or '(none yet)'}.")
    already = current.project_of.get(jid)
    if already and already != project:
        raise ValueError(f'"{name}" already belongs to "{already}". Moving it to another project is done by hand '
                         "in config/policy.toml.")
    data = read_toml(_target())
    target = data.setdefault("projects", {}).setdefault(project, {})
    if context and project not in projects:
        target["context"] = context
    chats = target.setdefault("chats", [])
    # A name shared with another chat grants nothing when the policy is resolved: keep the JID then.
    _, homonym = match(name, chat_index(conn), Identities(conn)) if name else (set(), True)
    entry = jid if homonym else name
    if entry not in chats:
        chats.append(entry)
    _save(data)
    return f'"{name}" now has content access in project "{project}".'


def revoke(conn: sqlite3.Connection, chat: str) -> str:
    name, jid = _resolve_one(conn, chat)
    data = read_toml(_target())
    removed = False
    chats, ids = chat_index(conn), Identities(conn)
    for section in [data.get("content", {}), *data.get("projects", {}).values()]:
        if "chats" in section:
            keep = [c for c in section["chats"] if jid not in match(c, chats, ids)[0]]
            removed |= len(keep) != len(section["chats"])
            section["chats"] = keep
    if not removed:
        if jid in resolve(conn, load_policy(paths.policy_file())).content:
            return (f'"{name}" was not added from the chat: it comes from config/policy.toml (or from a people '
                    "rule). Remove it by editing that file, or use whatsapp_exclude.")
        return f'"{name}" did not have content access.'
    _save(data)
    return f'"{name}" no longer has content access.'


def exclude(conn: sqlite3.Connection, chat: str) -> str:
    name, jid = _resolve_one(conn, chat)
    data = read_toml(_target())
    chats = data.setdefault("exclude", {}).setdefault("chats", [])
    if name not in chats:
        chats.append(name)
    _save(data)
    return f'"{name}" is now excluded: it will not appear even in the inbox.'
