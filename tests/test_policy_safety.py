"""Policy edits and name matching must never grant, keep or drop access other than what was asked."""

import threading

import pytest
from conftest import ANA_PN, cd

from whatsapp_context import exporter, paths, policy_edit
from whatsapp_context.paths import claude_file
from whatsapp_context.policy import Policy, Project, load_policy, parse_policy, read_toml, resolve
from whatsapp_context.store import connect

BASE = '[options]\ndays = 100000\n'


def _add(env, pk: int, jid: str, name: str) -> None:
    env["writer"].execute("INSERT INTO ZWACHATSESSION VALUES (?, ?, ?, ?, 0, 'x', NULL, 0, 0, 0, NULL)",
                          (pk, jid, name, cd(2026, 10, 20)))
    env["writer"].commit()


def _use(env, policy: str = "", claude: str | None = None) -> None:
    env["policy"].write_text(policy + "\n" + BASE)
    if claude is not None:
        claude_file(paths.policy_file()).write_text(claude)
    exporter.refresh()


def _sel():
    with connect() as conn:
        return resolve(conn, load_policy(paths.policy_file()))


def test_two_people_sharing_a_name_are_ambiguous(env):
    _add(env, 9, "34600000011@s.whatsapp.net", "Alex")
    _add(env, 10, "777@lid", "Alex")  # a different person: no Identities link
    _use(env, '[content]\nchats = ["Alex"]\n')
    sel = _sel()
    assert not {"34600000011@s.whatsapp.net", "777@lid"} & sel.content
    assert any("Alex" in u and "ambiguous" in u for u in sel.unmatched)


def test_linked_phone_and_lid_sessions_are_one_person(env):
    _add(env, 9, "900@lid", "Ana Investigadora")  # linked to ANA_PN by LID.sqlite
    _use(env, '[content]\nchats = ["Ana Investigadora"]\n')
    assert {ANA_PN, "900@lid"} <= _sel().content
    with connect() as conn:  # policy_edit accepts the linked pair too
        policy_edit.exclude(conn, "Ana Investigadora")
    saved = read_toml(claude_file(paths.policy_file()))["exclude"]["chats"]
    assert set(saved) == {ANA_PN, "900@lid"}
    assert not {ANA_PN, "900@lid"} & _sel().content


@pytest.mark.parametrize("name", ["222@g.us", "2026-2027"])
def test_edits_persist_the_jid_not_a_misleading_name(env, name):
    _add(env, 9, "888@g.us", name)
    _use(env)
    with connect() as conn:
        policy_edit.allow(conn, "888@g.us", "P", context="x")
    sel = _sel()
    assert "888@g.us" in sel.content and "222@g.us" not in sel.content
    with connect() as conn:
        policy_edit.exclude(conn, "888@g.us")
    sel = _sel()
    assert "888@g.us" in sel.excluded and "222@g.us" not in sel.excluded


def test_grant_survives_a_rename(env):
    _use(env)
    with connect() as conn:
        policy_edit.allow(conn, "Familia", "P", context="x")
    env["writer"].execute("UPDATE ZWACHATSESSION SET ZPARTNERNAME = 'Familia 2026' WHERE Z_PK = 3")
    env["writer"].commit()
    exporter.refresh()
    assert "222@g.us" in _sel().content


@pytest.mark.parametrize("policy, chat, reason", [
    ('[content]\npeople = ["Ana Investigadora"]\n', "Grupo nuevo con Ana", "people rule"),
    ('[projects."P"]\npatterns = ["^famil"]\n', "Familia", "pattern"),
    ('[content]\nchats = ["Familia"]\n', "Familia", "config/policy.toml"),
])
def test_revoke_reports_access_that_remains(env, policy, chat, reason):
    _use(env, policy)
    with connect() as conn:
        policy_edit.allow(conn, chat, "P", context="x")
        message = policy_edit.revoke(conn, chat)
    assert "still has content access" in message and reason in message and "whatsapp_exclude" in message
    assert "no longer" not in message


def test_edit_keeps_options_and_context_only_projects(env):
    _use(env, claude='[options]\ndays = 7\n\n[projects."Solo"]\ncontext = "~/solo"\n\n'
                     '[projects."Repo"]\nrepo = "~/dev/r"\npatterns = [\'^x\\d\']\n')
    with connect() as conn:
        policy_edit.allow(conn, "Familia", "Solo")
    claude = claude_file(paths.policy_file())
    assert parse_policy(read_toml(claude), claude) == Policy(days=7, projects=(
        Project("Repo", repo="~/dev/r", patterns=("^x\\d",)),
        Project("Solo", context="~/solo", chats=("222@g.us",)),
    ))


def test_concurrent_edits_do_not_lose_updates(env, monkeypatch):
    _use(env)
    a_read, b_read, a_done = threading.Event(), threading.Event(), threading.Event()
    real = policy_edit.read_toml

    def paused_read(path):
        data = real(path)
        if threading.current_thread().name == "A":  # without a lock, B reads the same old file meanwhile
            a_read.set()
            b_read.wait(1)
        else:
            b_read.set()
            a_done.wait(1)  # ...and writes after A, dropping A's exclusion
        return data

    monkeypatch.setattr(policy_edit, "read_toml", paused_read)
    errors: list[BaseException] = []

    def run(edit, done=None):
        try:
            with connect() as conn:
                edit(conn)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)
        if done:
            done.set()

    a = threading.Thread(target=run, name="A", args=(lambda c: policy_edit.exclude(c, "Contacto Privado"), a_done))
    b = threading.Thread(target=run, name="B", args=(lambda c: policy_edit.allow(c, "Familia", "P", context="x"),))
    a.start()
    assert a_read.wait(5)
    b.start()
    a.join(10)
    b.join(10)
    assert errors == []
    sel = _sel()
    assert "34600000009@s.whatsapp.net" in sel.excluded and "222@g.us" in sel.content
    lock = claude_file(paths.policy_file()).with_name("policy-claude.toml.lock")
    assert oct(lock.stat().st_mode & 0o777) == "0o600"
