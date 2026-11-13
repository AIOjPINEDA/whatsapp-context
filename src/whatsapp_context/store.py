"""Read-only queries over wa.sqlite (the filtered export written by WAExport).

Every query re-applies the policy, so removing a chat from config/policy.toml
hides it immediately, even before the next export.
"""

import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import quote

from . import paths
from .policy import GROUP, Identities, Selection, is_status, load_policy, resolve, table_names
from .text import LOCAL, clean, fmt, from_coredata, plural, quoted_reply, to_coredata

# ZMESSAGETYPE values (jonathanhowell/whatsapp-mcp docs/SCHEMA.md, MIT); None = plain text.
TYPES = {0: None, 1: "image", 2: "video", 3: "audio", 4: "contact", 5: "location", 6: "group event",
         7: None, 8: "document", 10: "notice", 11: "GIF", 14: "deleted message", 15: "sticker",
         46: "poll", 54: "video note", 59: "call", 66: "album"}
CAPTIONED = {1, 2, 3, 11, 15, 54}
SYSTEM = {6, 10}
NOT_A_REPLY = SYSTEM | {14}

# Columns this module reads. WAExport copies tables with SELECT *, so a WhatsApp update that
# renames one of these is caught here and fixed in Python, without rebuilding the helper.
REQUIRED = {
    "chats": {"Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZLASTMESSAGEDATE"},
    "messages": {"Z_PK", "ZCHATSESSION", "ZMESSAGEDATE", "ZISFROMME", "ZMESSAGETYPE", "ZTEXT", "ZFROMJID",
                 "ZGROUPMEMBER", "ZMEDIAITEM", "ZSTANZAID"},
    "media": {"Z_PK", "ZTITLE", "ZMEDIALOCALPATH", "ZMETADATA"},
    "group_members": {"Z_PK", "ZCHATSESSION", "ZMEMBERJID", "ZCONTACTNAME"},
}


NO_EXPORT = "No export yet: run uv run wa refresh"


class ChatNotFound(LookupError):
    pass


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    db = paths.export_db()
    if not db.exists():
        raise FileNotFoundError(NO_EXPORT)
    # The export is replaced atomically, so each open sees one consistent file.
    conn = sqlite3.connect(f"file:{quote(str(db))}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def check_schema(conn: sqlite3.Connection) -> list[str]:
    """Missing tables or columns, as 'table.column' strings (empty when all is well)."""
    missing = []
    for table, needed in REQUIRED.items():
        present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        missing += [f"{table}.{c}" for c in sorted(needed - present)] if present else [table]
    return missing


def exported_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'exported_at'").fetchone()
    return row[0] if row else None


def chat_rows(conn: sqlite3.Connection) -> list[dict]:
    """Every chat of the index as a dict: optional WhatsApp columns are read with .get()."""
    return [dict(r) for r in conn.execute("SELECT * FROM chats WHERE ZCONTACTJID IS NOT NULL")]


def last_activity(chat: dict) -> float:
    """Real date of the last message; ZLASTMESSAGEDATE is pushed into the future for pinned chats."""
    if chat.get("last_message_date"):
        return chat["last_message_date"]
    sort_date = chat["ZLASTMESSAGEDATE"] or 0
    return 0 if sort_date > to_coredata(datetime.now(LOCAL) + timedelta(days=1)) else sort_date


def latest_message(conn: sqlite3.Connection) -> datetime | None:
    """Most recent message in any chat: shows whether WhatsApp for Mac is still syncing."""
    return max((from_coredata(last_activity(c)) for c in chat_rows(conn) if last_activity(c)), default=None)


class Names:
    """Best-effort display names: group member name, address book, push name, LID name, number."""

    def __init__(self, conn: sqlite3.Connection):
        self.ids = Identities(conn)
        tables = table_names(conn)
        def named(table: str, *columns: str) -> list:
            """Rows whose first column (a name) is not empty; none when the table was not exported."""
            sql = f"SELECT {', '.join(columns)} FROM {table} WHERE {columns[0]} <> ''"
            return conn.execute(sql).fetchall() if table in tables else []
        contacts = {jid: n for n, *jids in named("contacts", "ZFULLNAME", "ZWHATSAPPID", "ZLID") for jid in jids if jid}
        push = {jid: n for n, jid in named("push_names", "ZPUSHNAME", "ZJID")}
        lids = named("lid_accounts", "ZDISPLAYNAME", "ZIDENTIFIER")
        lid_names = {i if "@" in (i or "") else f"{i}@lid": n for n, i in lids}
        self.sources = (contacts, push, lid_names)  # priority order

    def name(self, jid: str | None, member_name: str | None = None) -> str:
        return " ".join(clean(self._raw(jid, member_name)).split())

    def _raw(self, jid: str | None, member_name: str | None) -> str:
        if member_name:
            return member_name
        if not jid:
            return "?"
        aliases = [jid, *sorted(self.ids.aliases(jid) - {jid})]
        for source in self.sources:
            for alias in aliases:
                if alias in source:
                    return source[alias]
        user = jid.split("@")[0]
        return f"+{user}" if jid.endswith("@s.whatsapp.net") else user


def _pick(query: str, names: list[str], kind: str) -> str | None:
    """The one name equal to `query` (ignoring case and spacing), else the one containing it."""
    wanted = " ".join(query.split()).casefold()
    found = [n for n in names if n.casefold() == wanted] or [n for n in names if wanted in n.casefold()]
    if len(found) > 1:
        raise ChatNotFound(f'"{query}" matches several {kind}s; pick one: {", ".join(found)}.')
    return found[0] if found else None


def _indent(text: str) -> str:
    """Continuation lines are indented so message text can never look like a new message line."""
    return text.replace("\n", "\n    ")


class Store:
    """Policy-aware view of the export."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.policy = load_policy(paths.policy_file())
        self.sel: Selection = resolve(conn, self.policy)
        self.names = Names(conn)
        self._chats = {chat["ZCONTACTJID"]: chat for chat in chat_rows(conn)}
        labels = Counter(self._label(self._chats[j]) for j in self.sel.content if j in self._chats)
        self._repeated = {label for label, n in labels.items() if n > 1}

    def _label(self, row: dict) -> str:
        return " ".join(clean(row["ZPARTNERNAME"]).split()) or self.names.name(row["ZCONTACTJID"])

    def chat_label(self, row: dict) -> str:
        """Chat name; homonymous content chats also show their JID, which find_chat accepts."""
        label = self._label(row)
        return f"{label} [{row['ZCONTACTJID']}]" if label in self._repeated else label

    def _tagged(self, row: dict) -> str:
        """Chat label followed by its project, if it has one."""
        project = self.sel.project_of.get(row["ZCONTACTJID"])
        return self.chat_label(row) + (f" [{project}]" if project else "")

    def content_chats(self) -> list[dict]:
        rows = [self._chats[j] for j in self.sel.content if j in self._chats]
        return sorted(rows, key=lambda r: -last_activity(r))

    def find_chat(self, query: str) -> dict:
        """Find a content chat by JID, exact name or substring. Other chats are never matched."""
        chats = {self.chat_label(r): r for r in self.content_chats()}
        by_jid = {r["ZCONTACTJID"]: r for r in chats.values()}
        if query.strip() in by_jid:
            return by_jid[query.strip()]
        label = _pick(query, list(chats), "chat")
        if label is None:
            raise ChatNotFound(f'No chat with allowed content matches "{query}".')
        return chats[label]

    def _render(self, row: sqlite3.Row, quotes: dict[str, str]) -> str | None:
        mtype = row["ZMESSAGETYPE"]
        text = clean(row["ZTEXT"])
        if mtype in SYSTEM and not text:
            return None
        sender = ("Me" if row["ZISFROMME"] else
                  self.names.name(row["member_jid"] or row["ZFROMJID"], clean(row["member_name"])))
        parts = []
        label = TYPES.get(mtype, f"type {mtype}")
        if mtype == 8:
            status = "downloaded" if row["media_path"] else "not downloaded"
            parts.append(f"[document: {' '.join(text.split()) or 'untitled'} · {status}]")
            text = clean(row["media_title"])
        elif label:
            parts.append(f"[{label}]")
            if mtype in CAPTIONED:
                text = clean(row["media_title"]) or text
        quote_info = quoted_reply(row["media_meta"])
        if quote_info:
            stanza, quoted_jid, quoted_text = quote_info
            snippet = " ".join((clean(quoted_text) or quotes.get(stanza, "")).split())
            who = self.names.name(quoted_jid) if quoted_jid else "?"
            parts.append(f'(replying to {who}: "{snippet[:120]}")')
        if text:
            parts.append(_indent(text))
        return f"[{fmt(from_coredata(row['ZMESSAGEDATE']))}] {sender}: {' '.join(parts)}"

    def _messages_sql(self, where: str) -> str:
        return f"""
            SELECT m.Z_PK, m.ZCHATSESSION, m.ZMESSAGEDATE, m.ZISFROMME, m.ZMESSAGETYPE, m.ZTEXT, m.ZFROMJID,
                   m.ZSTANZAID, gm.ZMEMBERJID AS member_jid, gm.ZCONTACTNAME AS member_name,
                   mi.ZTITLE AS media_title, mi.ZMEDIALOCALPATH AS media_path, mi.ZMETADATA AS media_meta
            FROM messages m
            LEFT JOIN group_members gm ON gm.Z_PK = m.ZGROUPMEMBER
            LEFT JOIN media mi ON mi.Z_PK = m.ZMEDIAITEM
            WHERE {where}"""

    def messages(self, chat: str, since: datetime | None = None, until: datetime | None = None,
                 limit: int = 150) -> str:
        """Messages of one content chat; `until` is exclusive."""
        row = self.find_chat(chat)
        limit = max(1, min(limit, 1000))
        lo = to_coredata(since) if since else 0
        hi = to_coredata(until) if until else 1e12
        rows = self.conn.execute(
            self._messages_sql("m.ZCHATSESSION = ? AND m.ZMESSAGEDATE >= ? AND m.ZMESSAGEDATE < ?")
            + " ORDER BY m.ZMESSAGEDATE DESC, m.Z_PK DESC LIMIT ?",
            (row["Z_PK"], lo, hi, limit)).fetchall()
        rows.reverse()
        quotes = {r["ZSTANZAID"]: clean(r["ZTEXT"]) for r in rows if r["ZSTANZAID"]}
        lines = [line for r in rows if (line := self._render(r, quotes))]
        kind = "group" if row["ZCONTACTJID"].endswith(GROUP) else "one-to-one chat"
        header = f"## {self.chat_label(row)} ({kind}) · {plural(len(lines), 'message')}"
        if len(rows) == limit:
            header += " (truncated: there are more; narrow with since/until or raise limit)"
        jid = row["ZCONTACTJID"]
        project = self.sel.project_of.get(jid)
        if jid in self.sel.conflicts:
            header += (f"\nNo project: conflict between {' | '.join(self.sel.conflicts[jid])}. Do not combine it "
                       "with any other chat or project; ask the user to assign it.")
        elif not project:
            header += "\nNo project: do not use it in any project until the user assigns it."
        elif jid in self.sel.via_people:
            header += (f"\nProject: {project}{self._where(project)}. Group not assigned on its own (it came in "
                       f"through {self.sel.via_people[jid]}): do not combine it with other groups or projects; "
                       "propose where it belongs and stop.")
        else:
            rule = ("what is in this group stays in this project; do not use it in another one, even if they "
                    "share people." if jid.endswith(GROUP) else
                    'one-to-one chat: it may mix topics; assign each message to its project or mark it "unassigned".')
            header += f"\nProject: {project}{self._where(project)}. One project per session: {rule}"
        return "\n".join([header, *lines]) if lines else header + "\n(no messages in that range)"

    def _one_project(self, query: str) -> str:
        names = sorted(set(self.sel.project_of.values()))
        name = _pick(query, names, "project")
        if name is None:
            raise ChatNotFound(f'No project matches "{query}". Projects: {", ".join(names) or "(none)"}.')
        return name

    def _where(self, project: str) -> str:
        p = next(p for p in self.policy.projects if p.name == project)
        return (f" · context `{p.context}`" if p.context else "") + (f" · repo `{p.repo}`" if p.repo else "")

    def search(self, query: str, chat: str | None = None, since: datetime | None = None, limit: int = 50,
               project: str | None = None) -> str:
        """Search content chats; `project` restricts it to one project (substring of its name)."""
        if chat:
            pks = [self.find_chat(chat)["Z_PK"]]
        elif project:
            chosen = self._one_project(project)
            pks = [r["Z_PK"] for r in self.content_chats() if self.sel.project_of.get(r["ZCONTACTJID"]) == chosen]
        else:
            pks = [r["Z_PK"] for r in self.content_chats()]
        if not pks or not query.strip():
            return "No results."
        pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        marks = ",".join("?" * len(pks))
        rows = self.conn.execute(
            self._messages_sql(f"m.ZCHATSESSION IN ({marks}) AND m.ZMESSAGEDATE >= ? "
                               "AND (m.ZTEXT LIKE ? ESCAPE '\\' OR mi.ZTITLE LIKE ? ESCAPE '\\')")
            + " ORDER BY m.ZMESSAGEDATE DESC LIMIT ?",
            (*pks, to_coredata(since) if since else 0, pattern, pattern, max(1, min(limit, 500)))).fetchall()
        by_pk = {r["Z_PK"]: r for r in self.content_chats()}
        lines = [f"{self._tagged(by_pk[r['ZCHATSESSION']])} · {line}" for r in rows if (line := self._render(r, {}))]
        return "\n".join(lines) if lines else "No results."

    def inbox(self, hours: int = 24) -> str:
        """Recent activity: content chats with counts; other chats as metadata only."""
        since = to_coredata(datetime.now(LOCAL) - timedelta(hours=hours))
        skip = ",".join(str(t) for t in NOT_A_REPLY)
        content_lines, meta_lines = [], []
        for jid, row in sorted(self._chats.items(), key=lambda kv: -last_activity(kv[1])):
            if last_activity(row) < since or jid in self.sel.excluded or is_status(jid) or row.get("ZREMOVED"):
                continue
            kind = "group" if jid.endswith(GROUP) else "1:1"
            last = fmt(from_coredata(last_activity(row)))
            unread = row.get("ZUNREADCOUNT")
            unread_txt = f" · {unread} unread" if unread else ""
            if jid in self.sel.content:
                new, mine_last = self.conn.execute(
                    "SELECT count(CASE WHEN ZISFROMME = 0 AND ZMESSAGETYPE NOT IN (6, 10) THEN 1 END), "
                    "max(CASE WHEN ZISFROMME = 1 THEN ZMESSAGEDATE END) "
                    "FROM messages WHERE ZCHATSESSION = ? AND ZMESSAGEDATE >= ?", (row["Z_PK"], since)).fetchone()
                last_msg = self.conn.execute(
                    f"SELECT ZISFROMME FROM messages WHERE ZCHATSESSION = ? AND ZMESSAGETYPE NOT IN ({skip}) "
                    "ORDER BY ZMESSAGEDATE DESC LIMIT 1", (row["Z_PK"],)).fetchone()
                pending = " · awaiting your reply" if last_msg and not last_msg[0] else ""
                replied = f" · your last message {fmt(from_coredata(mine_last))}" if mine_last else ""
                content_lines.append(f"- {self._tagged(row)} ({kind}): {new} new from others, "
                                     f"last {last}{unread_txt}{pending}{replied}")
            else:
                meta_lines.append(f"- {self._label(row)} ({kind}): last {last}{unread_txt}")
        out = [f"## Activity in the last {hours} h", "", "### Content chats (you can read them)"]
        out += content_lines or ["(none)"]
        out += ["", "### Metadata only (text cannot be read)"]
        out += meta_lines or ["(none)"]
        return "\n".join(out)

    def chats_overview(self) -> str:
        by_project: dict[str | None, list[str]] = {}
        for row in self.content_chats():
            kind = "group" if row["ZCONTACTJID"].endswith(GROUP) else "1:1"
            first, count = self.conn.execute(
                "SELECT min(ZMESSAGEDATE), count(*) FROM messages WHERE ZCHATSESSION = ?", (row["Z_PK"],)).fetchone()
            jid = row["ZCONTACTJID"]
            via = (f" · via {self.sel.via_people[jid]}" if jid in self.sel.via_people else
                   f" · via pattern `{self.sel.via_pattern[jid]}`" if jid in self.sel.via_pattern else "")
            by_project.setdefault(self.sel.project_of.get(row["ZCONTACTJID"]), []).append(
                f"- {self.chat_label(row)} ({kind}): {plural(count, 'message')} exported since "
                f"{fmt(from_coredata(first))}, last {fmt(from_coredata(last_activity(row)))}{via}")
        lines = ["## Chats with allowed content, by project"] if by_project else [
            "No chats with content yet: add chats to config/policy.toml "
            "(find exact names with uv run wa chats --all --grep TEXT)."]
        for project, project_lines in sorted(by_project.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
            title = f"### {project}{self._where(project)}" if project else "### No project"
            lines += ["", title, *project_lines]
        notes = self.policy_notes()
        return "\n".join(lines + ([""] + notes if notes else []))

    def policy_notes(self) -> list[str]:
        """Policy entries that match no chat, and chats that several projects claim."""
        notes = [f"Policy entry with no match: {entry}" for entry in self.sel.unmatched]
        for jid, projects in self.sel.conflicts.items():
            name = self.chat_label(self._chats[jid]) if jid in self._chats else jid
            notes.append(f"Policy conflict: {name} is claimed by {' and '.join(projects)}; keep it in one project.")
        return notes
