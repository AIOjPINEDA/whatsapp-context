"""WAExport end to end on a synthetic container: read-only source, filtering before export."""

import sqlite3

from conftest import digest

from whatsapp_context import exporter, paths


def _export_rows(sql):
    conn = sqlite3.connect(paths.export_db())
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_source_files_are_never_modified(env):
    files = [env["source"] / f"ChatStorage.sqlite{s}" for s in ("", "-wal", "-shm")]
    assert all(f.exists() for f in files), "fixture must look like a live WAL database"
    before = [digest(f) for f in files]
    exporter.refresh()
    assert [digest(f) for f in files] == before


def test_only_selected_chats_leave_the_container(env):
    status = exporter.refresh()
    assert status["ok"], status
    texts = " ".join(t or "" for (t,) in _export_rows("SELECT ZTEXT FROM messages"))
    assert "Propuesta" in texts and "Nuevo hilo" in texts and "factura.pdf" in texts
    for secret in ("SECRETO-FAMILIA", "SECRETO-PRIVADO", "SECRETO-EXCLUIDO"):
        assert secret not in texts


def test_index_carries_no_message_text_drafts_or_media_keys(env):
    exporter.refresh()
    sql = "SELECT count(*) FROM chats WHERE ZLASTMESSAGETEXT IS NOT NULL OR ZSAVEDINPUT IS NOT NULL"
    assert _export_rows(sql) == [(0,)]
    assert _export_rows("SELECT count(*) FROM media WHERE ZMEDIAURL IS NOT NULL OR ZMEDIAKEY IS NOT NULL") == [(0,)]
    assert "ZNOTES" not in {r[1] for r in _export_rows("PRAGMA table_info(contacts)")}


def test_window_excludes_old_messages(env):
    env["policy"].write_text(env["policy"].read_text().replace("days = 100000", "days = 30"))
    exporter.refresh()
    texts = [t for (t,) in _export_rows("SELECT ZTEXT FROM messages")]
    assert "Mensaje muy antiguo" not in texts


def test_schema_change_fails_closed(env):
    env["writer"].execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZMESSAGEDATE TO ZDATE")
    env["writer"].commit()
    status = exporter.run_helper()
    assert status["ok"] is False and status["error_kind"] == "schema"


def test_unreadable_source_is_reported_as_missing_permission(env):
    db = env["source"] / "ChatStorage.sqlite"
    db.chmod(0)
    try:
        status = exporter.run_helper()
    finally:
        db.chmod(0o644)
    assert status["error_kind"] == "fda_denied"
