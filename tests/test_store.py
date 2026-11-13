"""Policy resolution and what Claude can see through the store."""

import pytest
from conftest import ANA_PN, CARMEN_LID

from whatsapp_context import exporter
from whatsapp_context.store import ChatNotFound, Store, connect
from whatsapp_context.text import from_coredata, parse_local, to_coredata


@pytest.fixture
def store(env):
    exporter.refresh()
    with connect() as conn:
        yield Store(conn)


def test_policy_resolution(store):
    assert store.sel.content == {"111@g.us", ANA_PN, CARMEN_LID, "333@g.us"}
    assert store.sel.via_people["333@g.us"] == "Ana Investigadora"
    assert "444@g.us" in store.sel.excluded and "444@g.us" not in store.sel.content
    assert "content: Chat que no existe" in store.sel.unmatched


def test_messages_render_names_dates_documents_and_quotes(store):
    thread = store.messages("grupo proyecto")
    assert "[2026-10-20 11:00] Ana López: Propuesta" in thread  # 09:00 UTC -> Madrid (CEST)
    assert "Me: De acuerdo" in thread
    assert '(replying to Ana López: "Propuesta: enviar el borrador") Perfecto' in thread
    carmen = store.messages("Carmen")
    assert "[document: factura.pdf · downloaded]" in carmen
    assert "[image] foto de la pizarra" in store.messages("Ana Investigadora")


def test_other_chats_never_return_text(store):
    for name in ("Contacto Privado", "Familia", "Grupo excluido"):
        with pytest.raises(ChatNotFound):
            store.messages(name)
    assert store.search("SECRETO") == "No results."


def test_inbox_shows_metadata_only_and_hides_excluded(store):
    inbox = store.inbox(hours=24 * 365 * 5)
    assert "Contacto Privado (1:1): last" in inbox
    assert "SECRETO" not in inbox
    assert "Grupo excluido" not in inbox
    assert "status@broadcast" not in inbox
    assert "Grupo Proyecto (group): 2 new from others, last 2026-10-20 13:00" in inbox  # real date, not the pinned one


def test_removing_a_chat_from_policy_hides_it_without_reexport(env, store):
    env["policy"].write_text('[content]\nchats = ["Carmen Gestoria"]\n')
    with connect() as conn:
        fresh = Store(conn)
        with pytest.raises(ChatNotFound):
            fresh.messages("Grupo Proyecto")


def test_date_round_trip():
    moment = parse_local("2026-03-29 03:30")  # just after the DST change in Madrid
    assert from_coredata(to_coredata(moment)) == moment


def test_local_zone_follows_env(monkeypatch):
    import importlib

    from whatsapp_context import text
    monkeypatch.setenv("WA_TZ", "America/Mexico_City")
    try:
        assert str(importlib.reload(text).LOCAL) == "America/Mexico_City"
        monkeypatch.setenv("WA_TZ", "Not/AZone")
        monkeypatch.delenv("TZ", raising=False)
        assert importlib.reload(text).LOCAL is not None  # falls back to the Mac's zone
    finally:
        monkeypatch.setenv("WA_TZ", "Europe/Madrid")
        importlib.reload(text)
