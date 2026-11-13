"""Which chats the assistant may read in full, and which ones never appear at all.

The policy lives in config/policy.toml (see config/policy.example.toml) plus policy-claude.toml next to it:
chats are named exactly as in WhatsApp, or by phone number; "people" pulls in every group where that
person is an active member; a project's "patterns" (regular expressions, case-insensitive) pull in every
chat whose name matches; "exclude" always wins. Precedence: exclude > explicit chats > patterns > people.

Unknown keys are an error: a typo must not silently grant or hide anything.
"""

import re
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import claude_file

GROUP = "@g.us"
PHONE = "@s.whatsapp.net"
LID = "@lid"
JID_RE = re.compile(r"[\w.+-]+@(g\.us|s\.whatsapp\.net|lid|broadcast)")

SECTION_KEYS = {"content": {"chats", "people"}, "exclude": {"chats"}, "options": {"days"}}
PROJECT_KEYS = {"context", "repo", "chats", "people", "patterns"}


@dataclass(frozen=True)
class Project:
    name: str
    context: str = ""
    chats: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    repo: str = ""
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    chats: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    days: int = 180
    projects: tuple[Project, ...] = ()


@dataclass
class Selection:
    content: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    unmatched: list[str] = field(default_factory=list)
    via_people: dict[str, str] = field(default_factory=dict)
    via_pattern: dict[str, str] = field(default_factory=dict)
    project_of: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)


def _merge(target: dict, key: str, value: object) -> None:
    """Lists are concatenated (without duplicates) and tables merged; for scalars the first one wins."""
    if key not in target:
        target[key] = value
        return
    old = target[key]
    if isinstance(old, list) and isinstance(value, list):
        target[key] = old + [v for v in value if v not in old]
    elif isinstance(old, dict) and isinstance(value, dict):
        for inner_key, inner in value.items():
            _merge(old, inner_key, inner)


def read_toml(path: Path) -> dict:
    """Parse a policy file; a missing file is an empty policy."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path.name} has a syntax error: {exc}") from exc


def _section(value: object, allowed: set[str], where: str, path: Path) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: {where} must be a table")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f'{path.name}: unknown key "{unknown[0]}" in {where} (valid: {", ".join(sorted(allowed))})')
    return value


def _strings(section: dict, key: str, path: Path) -> tuple[str, ...]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f'{path.name}: "{key}" must be a list of quoted strings')
    return tuple(value)


def parse_policy(data: dict, path: Path) -> Policy:
    """Build a Policy from a parsed file (or from what policy_edit is about to write); `path` names errors."""
    data = _section(data, {*SECTION_KEYS, "projects"}, "the top level", path)
    content, exclude, options = (_section(data.get(s, {}), SECTION_KEYS[s], f"[{s}]", path)
                                 for s in ("content", "exclude", "options"))
    if not isinstance(data.get("projects", {}), dict):
        raise ValueError(f"{path.name}: [projects] must be a table")
    projects = []
    for name, raw in data.get("projects", {}).items():
        section = _section(raw, PROJECT_KEYS, f'[projects."{name}"]', path)
        patterns = _strings(section, "patterns", path)
        for pattern in patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f'{path.name}: invalid pattern "{pattern}" in [projects."{name}"]: {exc}') from exc
        projects.append(Project(name, str(section.get("context", "")), _strings(section, "chats", path),
                                _strings(section, "people", path), str(section.get("repo", "")), patterns))
    days = options.get("days", 180)
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError(f'{path.name}: [options] "days" must be a positive whole number')
    return Policy(_strings(content, "chats", path), _strings(content, "people", path),
                  _strings(exclude, "chats", path), days, tuple(projects))


def load_policy(path: Path) -> Policy:
    """Read config/policy.toml plus policy-claude.toml; a missing policy means no content access."""
    merged: dict = {}
    for file in (path, claude_file(path)):
        data = read_toml(file)
        parse_policy(data, file)  # checked one by one so an error names its file
        for key, value in data.items():
            _merge(merged, key, value)
    return parse_policy(merged, path)


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _name(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _jid(value: str | None, suffix: str) -> str | None:
    digits = _digits((value or "").split("@")[0])
    return f"{digits}{suffix}" if digits else None


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def is_status(jid: str) -> bool:
    """Status and broadcast sessions share a contact's name but are never conversations."""
    return jid.startswith("status@") or jid.endswith(("status", "@broadcast"))


class Identities:
    """Maps a person's phone JID and privacy LID onto each other."""

    def __init__(self, conn: sqlite3.Connection):
        self._pairs: dict[str, set[str]] = {}
        tables = table_names(conn)
        pairs = []
        if "contacts" in tables:
            pairs += conn.execute("SELECT ZWHATSAPPID, ZLID FROM contacts").fetchall()
        if "lid_accounts" in tables:
            pairs += conn.execute("SELECT ZPHONENUMBER, ZIDENTIFIER FROM lid_accounts").fetchall()
        for phone, lid in pairs:
            phone, lid = _jid(phone, PHONE), _jid(lid, LID)
            if phone and lid:
                self._pairs.setdefault(phone, set()).add(lid)
                self._pairs.setdefault(lid, set()).add(phone)

    def aliases(self, jid: str) -> set[str]:
        return {jid} | self._pairs.get(jid, set())


def chat_index(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """(Z_PK, JID, name) of every chat in the export."""
    return [(pk, jid, name or "") for pk, jid, name in
            conn.execute("SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME FROM chats WHERE ZCONTACTJID IS NOT NULL")]


def _identity_of(entry: str, ids: Identities) -> set[str] | None:
    """A phone number or JID names a person directly, even without a 1:1 chat."""
    entry = entry.strip()
    if JID_RE.fullmatch(entry):  # a group name may contain "@" too
        return ids.aliases(entry)
    if not re.search(r"[^\d\s+().-]", entry) and len(_digits(entry)) >= 6:
        return ids.aliases(f"{_digits(entry)}{PHONE}")
    return None


def match(entry: str, chats: list[tuple[int, str, str]], ids: Identities) -> tuple[set[str], bool]:
    """Chats a policy entry refers to, and whether a name matched several different chats."""
    identity = _identity_of(entry, ids)
    if identity is not None:
        return {jid for _, jid, _ in chats if jid in identity}, False
    found = {jid for _, jid, name in chats if _name(name) == _name(entry)}
    # Sessions sharing a name are one person only when Identities links them (phone + @lid);
    # otherwise two people (or groups) share the name and it grants nothing.
    return found, len(found) > 1 and not any(found <= ids.aliases(j) for j in found)


def _assign(sel: Selection, owners: dict[str, set[str]]) -> None:
    """A chat claimed by one project belongs to it; claimed by several, to none until the user picks."""
    for jid, projects in owners.items():
        if jid in sel.excluded:
            continue
        if len(projects) == 1:
            sel.project_of[jid] = next(iter(projects))
        else:
            sel.conflicts[jid] = sorted(projects)


def resolve(conn: sqlite3.Connection, policy: Policy) -> Selection:
    """Turn the policy into chat JIDs using the exported index (no message text needed)."""
    chats = chat_index(conn)
    ids = Identities(conn)
    sel = Selection()

    for entry in policy.exclude:
        found, _ = match(entry, chats, ids)  # excluding every homonym is the safe side
        sel.excluded |= found
        if not found:
            sel.unmatched.append(f"exclude: {entry}")

    explicit: set[str] = set()
    owners: dict[str, set[str]] = {}
    for entry, project in [(c, None) for c in policy.chats] + [(c, p.name) for p in policy.projects for c in p.chats]:
        found, ambiguous = match(entry, chats, ids)
        if ambiguous:
            sel.unmatched.append(f"content: {entry} (ambiguous: {len(found)} chats; use the phone number)")
            continue
        if not found:
            sel.unmatched.append(f"content: {entry}")
        explicit |= found
        for jid in found if project else ():
            owners.setdefault(jid, set()).add(project)
    _assign(sel, owners)

    # Name patterns: below explicit chats, above people.
    rules = [(re.compile(p, re.IGNORECASE), p, proj.name) for proj in policy.projects for p in proj.patterns]
    by_pattern: dict[str, set[str]] = {}
    for _, jid, name in chats:
        if not name or jid in explicit or jid in sel.excluded or is_status(jid):
            continue
        for rule, pattern, project in rules:
            if rule.search(name):
                by_pattern.setdefault(jid, set()).add(project)
                sel.via_pattern.setdefault(jid, pattern)
    _assign(sel, by_pattern)
    claimed = explicit | set(by_pattern)

    member_cols = {r[1] for r in conn.execute("PRAGMA table_info(group_members)")}
    members = []
    if "ZMEMBERJID" in member_cols:
        active = "WHERE ZISACTIVE IS NULL OR ZISACTIVE = 1" if "ZISACTIVE" in member_cols else ""
        members = conn.execute(f"SELECT ZCHATSESSION, ZMEMBERJID FROM group_members {active}").fetchall()
    group_by_pk = {pk: jid for pk, jid, _ in chats if jid.endswith(GROUP)}
    by_people: dict[str, set[str]] = {}
    people = [(c, None) for c in policy.people] + [(c, p.name) for p in policy.projects for c in p.people]
    for person, project in people:
        identities = _identity_of(person, ids)
        if identities is None:
            found, ambiguous = match(person, chats, ids)
            found = {j for j in found if not j.endswith(GROUP)}
            if ambiguous or not found:
                sel.unmatched.append(f"people: {person}" + (" (ambiguous; use the phone number)" if ambiguous else ""))
                continue
            identities = set().union(*(ids.aliases(j) for j in found))
        for pk, member in members:
            jid = group_by_pk.get(pk)
            if jid and member in identities and jid not in claimed:
                sel.content.add(jid)
                sel.via_people.setdefault(jid, person)
                if project:
                    by_people.setdefault(jid, set()).add(project)
    _assign(sel, by_people)

    sel.content = (sel.content | claimed) - sel.excluded
    return sel
