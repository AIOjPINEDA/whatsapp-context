"""What `wa` prints: truthful status, conflicts, hints and refresh failures."""

import time

import pytest

from whatsapp_context import cli, exporter, paths
from whatsapp_context.store import Store, connect

DAYS = "\n[options]\ndays = 100000\n"


def wa(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_status_before_any_export(env, capsys):
    assert wa(capsys, "status") == (1, "No export yet: run uv run wa refresh\n")


def test_status_follows_both_policy_files(env, capsys):
    exporter.refresh()
    time.sleep(0.05)
    paths.claude_file(env["policy"]).write_text('[content]\nchats = ["Familia", "Nadie Prueba"]\n')
    code, out = wa(capsys, "status")
    assert code == 0 and "Policy entry with no match: content: Nadie Prueba" in out
    with connect() as conn:
        assert "SECRETO-FAMILIA" in Store(conn).messages("Familia")


def test_chat_in_two_projects_is_a_conflict(env, capsys):
    env["policy"].write_text('[projects."A"]\nchats = ["Familia"]\n\n[projects."B"]\nchats = ["Familia"]\n' + DAYS)
    code, out = wa(capsys, "refresh")
    assert "Policy conflict: Familia is claimed by A and B" in out
    assert "no match" not in out
    with connect() as conn:
        sel = Store(conn).sel
    assert "222@g.us" in sel.content and "222@g.us" not in sel.project_of


def test_all_chats_hide_status_and_excluded_and_mark_content(env, capsys):
    code, out = wa(capsys, "chats", "--all")
    assert code == 0 and "* Grupo Proyecto" in out and "  Familia" in out
    assert "status" not in out and "Grupo excluido" not in out


def test_all_chats_clean_third_party_names(env, capsys):
    env["writer"].execute("UPDATE ZWACHATSESSION SET ZPARTNERNAME = ? WHERE ZCONTACTJID = '222@g.us'",
                          ("Fam\u202eilia\x1b[31m @ casa",))
    env["writer"].commit()
    code, out = wa(capsys, "refresh")
    code, out = wa(capsys, "chats", "--all")
    assert "\u202e" not in out and "\x1b" not in out and "Familia[31m @ casa" in out


def test_empty_states_point_to_the_policy_not_to_mcp_tools(env, capsys):
    env["policy"].write_text(DAYS)
    _, chats = wa(capsys, "chats")
    _, inbox = wa(capsys, "inbox", "--hours", str(24 * 365 * 5))
    assert "No chats with content yet: add chats to config/policy.toml" in chats
    assert "whatsapp_" not in chats + inbox


def test_counts_are_pluralised(env, capsys):
    env["policy"].write_text('[content]\nchats = ["Carmen Gestoria"]\n' + DAYS)
    _, out = wa(capsys, "refresh")
    assert "1 message from 1 content chat" in out
    _, chats = wa(capsys, "chats")
    assert "1 message exported" in chats


@pytest.mark.parametrize("variable", ["WA_DATA_DIR", "WA_SOURCE_DIR"])
def test_locations_the_helper_refuses_are_explained(env, capsys, monkeypatch, variable):
    outside = paths.REPO / ".local" / "not-allowed"
    monkeypatch.setenv(variable, str(outside))
    code, out = wa(capsys, "refresh")
    assert code == 1 and out.startswith(f"Could not refresh: {variable} must be")
    assert not outside.exists()


def test_policy_errors_use_the_same_prefix(env, capsys):
    env["policy"].write_text("[excludes]\nchats = []\n")
    code, out = wa(capsys, "refresh")
    assert code == 1 and out.startswith('Could not refresh: policy.toml: unknown key "excludes"')
