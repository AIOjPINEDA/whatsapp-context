"""Freshness, error boundaries, the canary, chat discovery and MCP error results (review of nov 2026)."""

import json
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from conftest import ANA_PN, cd
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError

from whatsapp_context import cli, exporter, mcp_server, paths
from whatsapp_context.policy import load_policy
from whatsapp_context.store import ChatNotFound, Store, connect
from whatsapp_context.text import to_coredata

DAYS = "\n[options]\ndays = 100000\n"


def wa(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def add_familia(env) -> None:
    env["policy"].write_text(env["policy"].read_text().replace('"Grupo Proyecto",', '"Grupo Proyecto", "Familia",'))


def familia_thread() -> str:
    with connect() as conn:
        return Store(conn).messages("Familia")


def test_policy_edited_during_an_export_is_not_fresh(env, monkeypatch):
    run = exporter.run_helper

    def run_and_edit() -> dict:
        status = run()
        if "Familia" not in env["policy"].read_text():
            add_familia(env)
        return status
    monkeypatch.setattr(exporter, "run_helper", run_and_edit)
    exporter.refresh()
    monkeypatch.setattr(exporter, "run_helper", run)
    assert exporter.ensure_fresh()["ok"]
    assert "SECRETO-FAMILIA" in familia_thread()


def test_failed_second_pass_leaves_nothing_fresh(env, monkeypatch):
    exporter.refresh()
    add_familia(env)
    run, calls = exporter.run_helper, []

    def first_pass_only() -> dict:
        calls.append(1)
        if len(calls) > 1:
            raise exporter.ExportError("WAExport did not answer")
        return run()
    monkeypatch.setattr(exporter, "run_helper", first_pass_only)
    assert not exporter.refresh()["ok"]
    monkeypatch.setattr(exporter, "run_helper", run)
    assert exporter.ensure_fresh()["ok"]
    assert "SECRETO-FAMILIA" in familia_thread()


def test_unreadable_policy_is_reported_not_raised(env, capsys):
    env["policy"].chmod(0)
    try:
        status = exporter.refresh()
        code, _ = wa(capsys, "refresh")
    finally:
        env["policy"].chmod(0o600)
    assert status["ok"] is False and "Permission denied" in status["error"] and code == 1


def test_damaged_status_does_not_crash(env):
    exporter.refresh()
    paths.status_file().write_text(json.dumps({"ok": True, "exported_at": "yesterday"}))
    assert exporter.ensure_fresh()["ok"]
    assert "never" in exporter.describe({"ok": False, "exported_at": "2026-13-45"})


def test_mcp_freshness_errors_are_tool_errors(env, monkeypatch):
    def denied() -> dict:
        raise PermissionError("denied")
    monkeypatch.setattr(mcp_server, "ensure_fresh", denied)
    with pytest.raises(ToolError, match="denied"):
        mcp_server.whatsapp_inbox()


def test_canary_comes_first_everywhere(env, capsys, monkeypatch):
    monkeypatch.setattr(exporter, "caller_can_read_whatsapp", lambda: True)
    for argv in (["refresh"], ["read", "Grupo Proyecto"], ["search", "Propuesta"], ["inbox"], ["chats"]):
        assert wa(capsys, *argv)[1].startswith(exporter.CANARY_WARNING), argv
    mcp_server._canary.cache_clear()
    try:
        assert mcp_server.whatsapp_inbox().startswith(exporter.CANARY_WARNING)
    finally:
        mcp_server._canary.cache_clear()


def test_data_dir_is_made_private(env):
    env["data"].mkdir(mode=0o755)
    env["data"].chmod(0o755)
    assert exporter.refresh()["ok"]
    assert oct(env["data"].stat().st_mode & 0o777) == "0o700"


def test_data_dir_link_is_refused(env, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    env["data"].symlink_to(tmp_path / "elsewhere")
    status = exporter.refresh()
    assert status["ok"] is False and "must be a folder of your own" in status["error"]  # before running WAExport
    assert not any((tmp_path / "elsewhere").iterdir())


def test_search_intersects_chat_and_project(env):
    env["policy"].write_text('[projects."Alpha"]\nchats = ["Grupo Proyecto"]\n\n'
                             '[projects."Beta"]\nchats = ["Carmen Gestoria"]\n' + DAYS)
    exporter.refresh()
    with connect() as conn:
        store = Store(conn)
        with pytest.raises(ChatNotFound, match='not in project "Alpha"'):
            store.search("factura", chat="Carmen", project="Alpha")
        assert "Propuesta" in store.search("Propuesta", chat="Grupo Proyecto", project="Alpha")


def test_search_says_when_it_hits_the_limit(env, capsys):
    exporter.refresh()
    code, out = wa(capsys, "search", "e", "--limit", "1")
    assert code == 0 and out.rstrip().endswith("raise limit)") and "truncated at 1 result," in out
    assert "truncated" not in wa(capsys, "search", "Propuesta")[1]


def test_every_chat_lists_repeated_names_with_their_jid(env, capsys):
    env["writer"].executemany("INSERT INTO ZWACHATSESSION (Z_PK, ZCONTACTJID, ZPARTNERNAME, ZLASTMESSAGEDATE) "
                              "VALUES (?,?,?,?)", [(20, "34611111111@s.whatsapp.net", "Juan", cd(2020, 1, 1)),
                                                   (21, "34622222222@s.whatsapp.net", "Juan", cd(2020, 1, 1))])
    env["writer"].commit()
    _, out = wa(capsys, "chats", "--all", "--grep", "juan")
    assert "  Juan [34611111111@s.whatsapp.net]" in out and "  Juan [34622222222@s.whatsapp.net]" in out
    every = mcp_server.whatsapp_chats(all=True)
    assert "Juan [34622222222@s.whatsapp.net]" in every and "  Familia" in every and "* Grupo Proyecto" in every
    assert "Grupo excluido" not in every and "status@" not in every


def test_mcp_errors_come_back_as_is_error(env):
    async def call(name: str, arguments: dict):
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments)
    missing = anyio.run(call, "whatsapp_messages", {"chat": "Nadie Prueba"})
    assert missing.is_error and "No chat with allowed content" in missing.content[0].text
    found = anyio.run(call, "whatsapp_messages", {"chat": "Grupo Proyecto"})
    assert not found.is_error and "Propuesta" in found.content[0].text


def test_missing_column_of_an_optional_table_is_reported(env):
    env["writer"].execute("ALTER TABLE ZWAPROFILEPUSHNAME RENAME COLUMN ZPUSHNAME TO ZNAME")
    env["writer"].commit()
    status = exporter.refresh()
    assert status["error_kind"] == "schema" and "push_names.ZPUSHNAME" in status["error"]


def test_cutoff_moves_on_every_refresh(env):
    exporter.refresh()
    selection = json.loads(paths.selection_file().read_text())
    paths.selection_file().write_text(json.dumps({**selection, "since": selection["since"] - 23 * 3600}))  # 23 h old
    exporter.refresh()
    since = json.loads(paths.selection_file().read_text())["since"]
    expected = to_coredata(datetime.now(UTC) - timedelta(days=load_policy(paths.policy_file()).days))
    assert abs(since - expected) < 120


def test_unnamed_chat_lists_its_jid(env, capsys):
    env["writer"].execute("UPDATE ZWACHATSESSION SET ZPARTNERNAME = NULL WHERE ZCONTACTJID = ?", (ANA_PN,))
    env["writer"].commit()
    exporter.refresh()
    with connect() as conn:
        listing = Store(conn).all_chats()
    assert f"[{ANA_PN}]" in listing


def test_listing_never_lets_a_name_impersonate_another_chat(env):
    env["writer"].execute("UPDATE ZWACHATSESSION SET ZPARTNERNAME = '222@g.us' WHERE ZCONTACTJID = '111@g.us'")
    env["writer"].commit()
    exporter.refresh()
    with connect() as conn:
        line = next(x for x in Store(conn).all_chats("222@g.us").splitlines() if "[111@g.us]" in x)
    assert line.endswith("222@g.us [111@g.us]")  # the real JID is what the user copies


def test_app_built_for_a_newer_macos_is_explained(env, monkeypatch, tmp_path):
    app = tmp_path / "WAExport.app"
    app.mkdir()
    monkeypatch.delenv("WA_HELPER_BIN")
    monkeypatch.setenv("WA_HELPER_APP", str(app))
    refused = "LSOpenURLsWithRole() failed with error -10825 for the file /Applications/WAExport.app."
    monkeypatch.setattr(exporter.subprocess, "run",
                        lambda *a, **k: exporter.subprocess.CompletedProcess(a, 1, "", refused))
    with pytest.raises(exporter.ExportError, match="newer macOS .*make setup"):
        exporter.run_helper()
