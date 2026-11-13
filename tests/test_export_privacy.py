"""WAExport never writes redacted values to disk, not even into freed pages, and keeps query indexes."""

import sqlite3

from whatsapp_context import exporter, paths

MARKERS = {name: f"MARKER-{name}-" + "x" * 5000 for name in ("LASTTEXT", "DRAFT", "MEDIAURL", "MEDIAKEY")}


def test_redacted_values_never_reach_the_export_file(env):
    w = env["writer"]
    w.execute("UPDATE ZWACHATSESSION SET ZLASTMESSAGETEXT = ?, ZSAVEDINPUT = ?",
              (MARKERS["LASTTEXT"], MARKERS["DRAFT"]))
    w.execute("UPDATE ZWAMEDIAITEM SET ZMEDIAURL = ?, ZMEDIAKEY = ?",
              (MARKERS["MEDIAURL"], MARKERS["MEDIAKEY"].encode()))
    w.commit()
    status = exporter.refresh()
    assert status["ok"], status
    files = list(paths.export_db().parent.glob("wa.sqlite*"))
    assert paths.export_db() in files
    for f in files:
        raw = f.read_bytes()
        for name in MARKERS:
            assert f"MARKER-{name}-".encode() not in raw, f"{name} found in {f.name}"


def test_export_has_the_indexes_queries_rely_on(env):
    assert exporter.refresh()["ok"]
    conn = sqlite3.connect(paths.export_db())
    try:
        indexed = {(t, c) for (t,) in conn.execute("SELECT DISTINCT tbl_name FROM sqlite_master WHERE type='index'")
                   for i in conn.execute(f"PRAGMA index_list({t})")
                   for (_, _, c) in conn.execute(f"PRAGMA index_info({i[1]})")}
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM messages WHERE ZCHATSESSION = 1 AND ZMESSAGEDATE >= 0"))
    finally:
        conn.close()
    assert "USING INDEX" in plan
    assert {("media", "Z_PK"), ("group_members", "Z_PK")} <= indexed
