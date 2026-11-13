"""Policy file format: English keys only, strict validation, round trip through policy_edit."""

import re
import shutil
import tomllib

import pytest
from conftest import REPO

from whatsapp_context import exporter, paths, policy_edit
from whatsapp_context.paths import claude_file
from whatsapp_context.policy import load_policy, read_toml, resolve
from whatsapp_context.store import connect


def test_bad_values_name_the_key(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('[content]\npeople = "Ana"\n')
    with pytest.raises(ValueError, match='"people" must be a list'):
        load_policy(path)
    path.write_text("[options]\ndays = \"many\"\n")
    with pytest.raises(ValueError, match='"days" must be a positive whole number'):
        load_policy(path)


def test_policy_edit_round_trips(env):
    env["policy"].write_text('[projects."Gestoría"]\nchats = ["+34 600 000 005"]\n\n[options]\ndays = 100000\n')
    claude = claude_file(paths.policy_file())
    claude.write_text('[content]\nchats = ["Ana Investigadora"]\npeople = ["Ana Investigadora"]\n\n'
                      '[projects."Personal"]\ncontext = "Familia"\npatterns = ["^nada$"]\n'
                      'chats = ["Contacto Privado"]\n\n'
                      '[exclude]\nchats = ["Grupo excluido con Ana"]\n')
    exporter.refresh()
    before = load_policy(paths.policy_file())
    with connect() as conn:
        policy_edit.allow(conn, "Familia", "Personal")
    saved = claude.read_text()
    raw = tomllib.loads(saved)
    assert set(raw) == {"content", "projects", "exclude"}
    assert set(raw["projects"]["Personal"]) == {"context", "patterns", "chats"}
    assert set(raw["content"]) == {"chats", "people"}
    after = load_policy(paths.policy_file())
    personal = next(p for p in after.projects if p.name == "Personal")
    assert personal.context == "Familia" and personal.patterns == ("^nada$",)
    assert personal.chats == ("Contacto Privado", "222@g.us")
    assert (after.chats, after.people, after.exclude) == (before.chats, before.people, before.exclude)
    assert read_toml(claude) == raw  # rewriting an English file changes nothing


def test_example_policy_parses_and_grants_nothing(env, tmp_path):
    example = tmp_path / "example" / "policy.toml"
    example.parent.mkdir()
    shutil.copy(REPO / "config" / "policy.example.toml", example)
    raw = tomllib.loads(example.read_text())
    assert set(raw) <= {"content", "projects", "exclude", "options"}  # ships with the canonical keys
    policy = load_policy(example)
    assert (policy.chats, policy.people, policy.exclude, policy.projects) == ((), (), (), ())
    assert policy.days == 180
    exporter.refresh()
    with connect() as conn:
        sel = resolve(conn, policy)
    assert sel.content == set() and sel.unmatched == []


@pytest.mark.parametrize("text, key, where", [
    ("[excludes]\nchats = []\n", "excludes", "the top level"),
    ('[content]\nchat = ["A"]\n', "chat", "[content]"),
    ('[projects."P"]\nfolder = "~/p"\n', "folder", '[projects."P"]'),
    ("[options]\ndays = 5\nday = 5\n", "day", "[options]"),
])
def test_unknown_keys_are_errors_naming_file_and_key(tmp_path, text, key, where):
    claude = claude_file(tmp_path / "policy.toml")
    claude.write_text(text)
    with pytest.raises(ValueError, match=f'^policy-claude.toml: unknown key "{key}" in {re.escape(where)}'):
        load_policy(tmp_path / "policy.toml")
