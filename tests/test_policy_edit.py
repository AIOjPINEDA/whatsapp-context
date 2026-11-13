"""Projects in the policy and changes requested from a chat (policy-claude.toml)."""

import pytest

from whatsapp_context import exporter, paths, policy_edit
from whatsapp_context.paths import claude_file
from whatsapp_context.store import ChatNotFound, Store, connect

PROJECTS = """
[projects."Investigación Ana"]
context = "Documentos/investigacion"
repo = "~/dev/proyecto-a"
chats = ["Grupo Proyecto"]
people = ["Ana Investigadora"]

[projects."Gestoría"]
chats = ["+34 600 000 005"]

[exclude]
chats = ["Grupo excluido con Ana"]

[options]
days = 100000
"""


@pytest.fixture
def store_env(env):
    env["policy"].write_text(PROJECTS)
    exporter.refresh()
    return env


def test_projects_grant_content_and_label_chats(store_env):
    with connect() as conn:
        store = Store(conn)
        assert store.sel.project_of["111@g.us"] == "Investigación Ana"
        assert store.sel.project_of["333@g.us"] == "Investigación Ana"  # via persona
        assert store.sel.project_of["555@lid"] == "Gestoría"
        overview = store.chats_overview()
    assert "### Investigación Ana · context `Documentos/investigacion` · repo `~/dev/proyecto-a`" in overview
    assert "### Gestoría" in overview


def test_allow_from_chat_writes_only_the_claude_file(store_env):
    original = store_env["policy"].read_text()
    with connect() as conn:
        message = policy_edit.allow(conn, "Familia", "Personal", context="Familia")
    assert "Familia" in message
    assert store_env["policy"].read_text() == original
    assert "Familia" in claude_file(paths.policy_file()).read_text()
    exporter.refresh()
    with connect() as conn:
        store = Store(conn)
        assert "SECRETO-FAMILIA" in store.messages("Familia")
        assert store.sel.project_of["222@g.us"] == "Personal"


def test_excluded_chat_answers_like_a_missing_one(store_env):
    with connect() as conn:
        for change in (lambda c: policy_edit.allow(conn, c, "Gestoría"), lambda c: policy_edit.revoke(conn, c),
                       lambda c: policy_edit.exclude(conn, c)):
            with pytest.raises(ValueError, match="named exactly"):
                change("Grupo excluido con Ana")
            with pytest.raises(ValueError, match="named exactly"):
                change("444@g.us")


def test_allow_requires_an_exact_existing_name(store_env):
    with connect() as conn, pytest.raises(ValueError, match="named exactly"):
        policy_edit.allow(conn, "Famil", "Gestoría")


def test_revoke_only_undoes_chat_additions(store_env):
    with connect() as conn:
        policy_edit.allow(conn, "Familia", "Gestoría")
        assert "no longer has" in policy_edit.revoke(conn, "Familia")
        assert "policy.toml" in policy_edit.revoke(conn, "Grupo Proyecto")
    exporter.refresh()
    with connect() as conn, pytest.raises(ChatNotFound):
        Store(conn).messages("Familia")


def test_exclude_from_chat_hides_even_metadata(store_env):
    with connect() as conn:
        policy_edit.exclude(conn, "Contacto Privado")
    with connect() as conn:
        assert "Contacto Privado" not in Store(conn).inbox(hours=24 * 365 * 5)


def test_thread_header_carries_project_and_round_rule(store_env):
    with connect() as conn:
        thread = Store(conn).messages("Grupo Proyecto")
    assert "Project: Investigación Ana · context `Documentos/investigacion`" in thread
    assert "One project per session" in thread


def test_search_can_be_limited_to_one_project(store_env):
    with connect() as conn:
        store = Store(conn)
        everywhere = store.search("a")
        only_ana = store.search("a", project="Investigación Ana")
        with pytest.raises(ChatNotFound, match="Projects"):
            store.search("a", project="no existe")
    assert "[Gestoría]" in everywhere and "[Gestoría]" not in only_ana
    assert "[Investigación Ana]" in only_ana


def test_allow_needs_an_existing_project_or_a_context(store_env):
    with connect() as conn, pytest.raises(ValueError, match="no project named"):
        policy_edit.allow(conn, "Familia", "Proyecto inventado")


def test_allow_never_moves_a_chat_between_projects(store_env):
    with connect() as conn, pytest.raises(ValueError, match="already belongs"):
        policy_edit.allow(conn, "Grupo Proyecto", "Gestoría")


def test_ambiguous_project_filter_is_refused(store_env):
    store_env["policy"].write_text(PROJECTS + '\n[projects."Investigación Otra"]\nchats = ["Familia"]\n')
    exporter.refresh()
    with connect() as conn, pytest.raises(ChatNotFound, match="several projects"):
        Store(conn).search("a", project="Investigación")


def test_group_reached_by_people_of_two_projects_gets_no_project(store_env):
    store_env["policy"].write_text(PROJECTS + '\n[projects."Otra línea"]\npeople = ["Ana Investigadora"]\n')
    exporter.refresh()
    with connect() as conn:
        store = Store(conn)
        assert "333@g.us" in store.sel.conflicts and "333@g.us" not in store.sel.project_of
        assert "conflict between" in store.messages("Grupo nuevo con Ana")


def test_claude_file_is_private(store_env):
    with connect() as conn:
        policy_edit.exclude(conn, "Contacto Privado")
    assert oct(claude_file(paths.policy_file()).stat().st_mode & 0o777) == "0o600"
