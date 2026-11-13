"""`wa`: the same views as the MCP server, for your own terminal."""

import argparse
import sqlite3
import sys

from . import paths
from .exporter import describe, ensure_fresh, refresh, report, with_canary
from .store import NO_EXPORT, ChatNotFound, Store, connect
from .text import parse_local, parse_until


def parser() -> argparse.ArgumentParser:
    """The command line, also read by the documentation check in tests."""
    root = argparse.ArgumentParser(prog="wa", description="Read-only, policy-filtered WhatsApp context")
    sub = root.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="export again and apply the policy")
    sub.add_parser("status", help="state of the export under the current policy")
    chats = sub.add_parser("chats", help="content chats (or every chat with --all)")
    chats.add_argument("--all", action="store_true",
                       help="every chat name, to write the policy (* = has content; [JID] when names repeat)")
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
    search.add_argument("--limit", type=int, default=50, help="maximum results, most recent first (default 50)")
    return root


def _run(args: argparse.Namespace) -> tuple[int, str]:
    if args.cmd == "status" and not paths.export_db().exists():
        return 1, NO_EXPORT
    if args.cmd in ("refresh", "status"):
        ok, text = report(refresh() if args.cmd == "refresh" else ensure_fresh())
        return (0 if ok else 1), text
    status = ensure_fresh()
    with connect() as conn:
        store = Store(conn)
        if args.cmd == "chats":
            body = store.all_chats(args.grep) if args.all else store.chats_overview()
        elif args.cmd == "inbox":
            body = store.inbox(args.hours)
        elif args.cmd == "read":
            body = store.messages(args.chat, parse_local(args.since), parse_until(args.until), args.limit)
        else:
            body = store.search(args.query, args.chat, parse_local(args.since), args.limit, args.project)
        return 0, f"{describe(status, conn)}\n\n{body}"


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        code, text = _run(args)
    except (OSError, ChatNotFound, ValueError, sqlite3.Error) as exc:
        print(with_canary(str(exc)), file=sys.stderr)
        return 1
    print(with_canary(text))
    return code


if __name__ == "__main__":
    sys.exit(main())
