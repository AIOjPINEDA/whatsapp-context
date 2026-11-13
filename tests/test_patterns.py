"""Name patterns in projects (`patterns`) and chat-driven edits that must not lose them."""

import pytest
from conftest import cd

from whatsapp_context import exporter, paths, policy_edit
from whatsapp_context.paths import claude_file
from whatsapp_context.policy import load_policy
from whatsapp_context.store import Store, connect


def _use(env, policy: str) -> None:
    env["policy"].write_text(policy + "\n[options]\ndays = 100000\n")
    exporter.refresh()


def test_pattern_grants_content_and_project(env):
    _use(env, '[projects."Personal"]\ncontext = "Familia"\npatterns = ["^famil"]\n')
    with connect() as conn:
        store = Store(conn)
        assert store.sel.project_of["222@g.us"] == "Personal"
        assert "SECRETO-FAMILIA" in store.messages("Familia")
        assert "via pattern `^famil`" in store.chats_overview()
        assert "SECRETO-PRIVADO" not in store.search("SECRETO")


def test_exclusion_wins_over_pattern(env):
    _use(env, '[projects."Ana"]\npatterns = ["\\\\bAna\\\\b"]\n\n[exclude]\nchats = ["Grupo excluido con Ana"]\n')
    with connect() as conn:
        sel = Store(conn).sel
    assert "333@g.us" in sel.content and "444@g.us" not in sel.content


def test_explicit_chat_wins_over_pattern(env):
    _use(env, '[projects."A"]\nchats = ["Grupo Proyecto"]\n\n[projects."B"]\npatterns = ["reboot"]\n')
    with connect() as conn:
        assert Store(conn).sel.project_of["111@g.us"] == "A"


def test_pattern_wins_over_people(env):
    _use(env, '[projects."A"]\npeople = ["Ana Investigadora"]\n\n'
              '[projects."B"]\npatterns = ["^grupo nuevo"]\n')
    with connect() as conn:
        sel = Store(conn).sel
    assert sel.project_of["333@g.us"] == "B" and "333@g.us" not in sel.conflicts
    assert sel.project_of["111@g.us"] == "A"  # Grupo Proyecto still arrives through Ana


def test_two_projects_matching_one_chat_is_a_conflict(env):
    _use(env, '[projects."A"]\npatterns = ["famil"]\n\n[projects."B"]\npatterns = ["ilia$"]\n')
    with connect() as conn:
        store = Store(conn)
        assert store.sel.conflicts["222@g.us"] == ["A", "B"]
        assert "222@g.us" not in store.sel.project_of
        assert "conflict between" in store.messages("Familia")


def test_invalid_pattern_names_its_file(env):
    env["policy"].write_text('[projects."A"]\npatterns = ["(unclosed"]\n')
    with pytest.raises(ValueError, match="policy.toml.*invalid pattern"):
        load_policy(env["policy"])
    status = exporter.ensure_fresh()
    assert status["error_kind"] == "policy" and "invalid pattern" in status["error"]


def test_allow_keeps_the_jid_of_a_homonym(env):
    env["writer"].execute("INSERT INTO ZWACHATSESSION VALUES (9, '999@g.us', 'Familia', ?, 0, 'x', NULL, 0, 0, 0, "
                          "NULL)",
                          (cd(2026, 10, 20),))
    env["writer"].commit()
    _use(env, '[projects."Gestoría"]\nchats = ["+34 600 000 005"]\n')
    with connect() as conn:
        policy_edit.allow(conn, "222@g.us", "Personal", context="Familia")
    saved = claude_file(paths.policy_file()).read_text()
    assert '"222@g.us"' in saved and '"Familia"]' not in saved
    exporter.refresh()
    with connect() as conn:
        sel = Store(conn).sel
    assert sel.project_of["222@g.us"] == "Personal" and "999@g.us" not in sel.content


def test_allow_preserves_hand_written_keys(env):
    _use(env, '[projects."Gestoría"]\nchats = ["+34 600 000 005"]\n')
    claude_file(paths.policy_file()).write_text(
        '[projects."Personal"]\ncontext = "Familia"\nrepo = "~/dev/x"\npatterns = ["^nada$"]\n'
        'people = ["Ana Investigadora"]\nchats = ["Contacto Privado"]\n')
    with connect() as conn:
        policy_edit.allow(conn, "Familia", "Personal")
    saved = load_policy(paths.policy_file())
    personal = next(p for p in saved.projects if p.name == "Personal")
    assert personal.patterns == ("^nada$",) and personal.people == ("Ana Investigadora",)
    assert personal.repo == "~/dev/x" and set(personal.chats) == {"Contacto Privado", "Familia"}
