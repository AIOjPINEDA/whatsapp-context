"""`wa`: the same views as the MCP server, for your own terminal."""

import argparse
import sqlite3
import sys

from . import paths
from .exporter import describe, ensure_fresh, refresh, report
from .policy import is_status
from .store import NO_EXPORT, ChatNotFound, Store, connect
from .text import clean, parse_local, parse_until


def parser() -> argparse.ArgumentParser:
    """The command line, also read by the documentation check in tests."""
    root = argparse.ArgumentParser(prog="wa", description="Read-only, policy-filtered WhatsApp context")
    sub = root.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="export again and apply the policy")
    sub.add_parser("status", help="state of the export under the current policy")
    chats = sub.add_parser("chats", help="content chats (or every chat with --all)")
    chats.add_argument("--all", action="store_true", help="every chat name, to write the policy (* = has content)")
    chats.add_argument("--grep", default="", help="only names containing this text")
    inbox = sub.add_parser("inbox", help="recent activity")
    inbox.add_argument("--hours", type=int, default=24, help="how far back to look (default 24)")
    read = sub.add_parser("read", help="messages of one allowed chat")
    read.add_argument("chat", help="chat name (or part of it)")
    read.add_argument("--since", help="YYYY-MM-DD or 'YYYY-MM-DD HH:MM'")
    read.add_argument("--until", help="YYYY-MM-DD (includes that day) or 'YYYY-MM-DD HH:MM'")
    read.add_argument("--limit", type=int, default=150, help="maximum messages, most recent first (default 150)")
    search = sub.add_parser("search", help="search text in the allowed chats")
    search.add_argument("query", help="text to look for")
    search.add_argument("--chat", help="limit to one chat")
    search.add_argument("--since", help="YYYY-MM-DD")
    search.add_argument("--project", help="limit to one project")
    return root


def _all_chats(store: Store, grep: str) -> str:
    rows = store.conn.execute("SELECT ZPARTNERNAME, ZCONTACTJID FROM chats WHERE ZCONTACTJID IS NOT NULL "
                              "ORDER BY ZPARTNERNAME").fetchall()
    names = [(jid, " ".join(clean(name).split()) or jid) for name, jid in rows
             if not is_status(jid) and jid not in store.sel.excluded]  # names are third-party text
    return "\n".join(f"{'*' if jid in store.sel.content else ' '} {name}" for jid, name in names
                     if grep.casefold() in name.casefold())


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.cmd == "status" and not paths.export_db().exists():
            print(NO_EXPORT)
            return 1
        if args.cmd in ("refresh", "status"):
            ok, text = report(refresh() if args.cmd == "refresh" else ensure_fresh())
            print(text)
            return 0 if ok else 1
        status = ensure_fresh()
        with connect() as conn:
            store = Store(conn)
            print(describe(status, conn), end="\n\n")
            if args.cmd == "chats":
                print(_all_chats(store, args.grep) if args.all else store.chats_overview())
            elif args.cmd == "inbox":
                print(store.inbox(args.hours))
            elif args.cmd == "read":
                print(store.messages(args.chat, parse_local(args.since), parse_until(args.until), args.limit))
            else:
                print(store.search(args.query, args.chat, parse_local(args.since), project=args.project))
    except (FileNotFoundError, ChatNotFound, ValueError, sqlite3.Error) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
