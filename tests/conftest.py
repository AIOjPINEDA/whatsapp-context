"""Synthetic WhatsApp container (no real data) and a real WAExport run against it."""

import hashlib
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ["WA_TZ"] = "Europe/Madrid"  # fixtures and expected times are written for this zone; set before import

from whatsapp_context import exporter
from whatsapp_context.text import to_coredata

REPO = Path(__file__).resolve().parents[1]
BINARY = REPO / "build" / "WAExport.app" / "Contents" / "MacOS" / "WAExport"

ANA_PN, ANA_LID = "34600000001@s.whatsapp.net", "900@lid"
CARMEN_LID = "555@lid"


def cd(y, mo, d, h=12, mi=0):
    return to_coredata(datetime(y, mo, d, h, mi, tzinfo=UTC))


def varint(n):
    out = b""
    while n > 0x7F:
        out += bytes([(n & 0x7F) | 0x80])
        n >>= 7
    return out + bytes([n])


def pb(*fields):
    """Minimal protobuf encoder: (number, bytes) pairs, length-delimited."""
    return b"".join(varint((number << 3) | 2) + varint(len(value)) + value for number, value in fields)


CHATS = [  # pk, jid, name, last, unread, last_text
    (1, "111@g.us", "Grupo Proyecto", cd(9026, 10, 20), 2, "texto"),  # pinned: far-future sort date
    (2, ANA_PN, "Ana Investigadora", cd(2026, 10, 19), 0, "texto"),
    (3, "222@g.us", "Familia", cd(2026, 10, 20), 5, "cena"),
    (4, "34600000009@s.whatsapp.net", "Contacto Privado", cd(2026, 10, 20), 1, "SECRETO-INDEX"),
    (5, CARMEN_LID, "Carmen Gestoria", cd(2026, 10, 18), 0, "factura"),
    (6, "333@g.us", "Grupo nuevo con Ana", cd(2026, 10, 20), 0, "hola"),
    (7, "444@g.us", "Grupo excluido con Ana", cd(2026, 10, 20), 0, "x"),
    (8, "status@broadcast", None, cd(2026, 10, 20), 0, None),
]

MESSAGES = [  # pk, chat, date, from_me, type, text, member_pk, media_pk, stanza
    (1, 1, cd(2026, 10, 20, 9), 0, 0, "Propuesta: enviar el borrador el viernes", 1, None, "S1"),
    (2, 1, cd(2026, 10, 20, 10), 1, 0, "De acuerdo", None, None, "S2"),
    (3, 1, cd(2026, 10, 20, 11), 0, 0, "Perfecto", 1, 3, "S3"),
    (4, 3, cd(2026, 10, 20, 11), 0, 0, "SECRETO-FAMILIA", 2, None, "S4"),
    (5, 4, cd(2026, 10, 20, 11), 0, 0, "SECRETO-PRIVADO", None, None, "S5"),
    (6, 5, cd(2026, 10, 18, 10), 0, 8, "factura.pdf", None, 1, "S6"),
    (7, 6, cd(2026, 10, 20, 8), 0, 0, "Nuevo hilo", 3, None, "S7"),
    (8, 7, cd(2026, 10, 20, 8), 0, 0, "SECRETO-EXCLUIDO", 4, None, "S8"),
    (9, 2, cd(2025, 1, 1, 8), 0, 0, "Mensaje muy antiguo", None, None, "S9"),
    (10, 2, cd(2026, 10, 19, 8), 0, 1, None, None, 2, "S10"),
]


def build_container(root: Path) -> sqlite3.Connection:
    """Create ChatStorage/ContactsV2/LID in WAL mode; returns an open writer connection."""
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "ChatStorage.sqlite")
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT,
            ZLASTMESSAGEDATE REAL, ZUNREADCOUNT INTEGER, ZLASTMESSAGETEXT TEXT, ZSAVEDINPUT TEXT,
            ZSESSIONTYPE INTEGER, ZREMOVED INTEGER, ZARCHIVED INTEGER, ZLASTMESSAGE INTEGER);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL,
            ZISFROMME INTEGER, ZMESSAGETYPE INTEGER, ZTEXT TEXT, ZFROMJID TEXT, ZGROUPMEMBER INTEGER,
            ZMEDIAITEM INTEGER, ZSTANZAID TEXT);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZTITLE TEXT,
            ZMEDIALOCALPATH TEXT, ZMEDIAURL TEXT, ZMEDIAKEY BLOB, ZMETADATA BLOB);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMEMBERJID TEXT,
            ZCONTACTNAME TEXT, ZISACTIVE INTEGER);
        CREATE TABLE ZWAGROUPINFO (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZCREATORJID TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
    """)
    db.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?,'borrador',0,0,0,NULL)", CHATS)
    db.execute("UPDATE ZWACHATSESSION SET ZLASTMESSAGE = 3 WHERE Z_PK = 1")
    db.executemany("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,NULL,?,?,?)", MESSAGES)
    quote = pb((5, b"S1"), (6, ANA_LID.encode()), (19, pb((1, b"Propuesta: enviar el borrador"))))
    db.executemany("INSERT INTO ZWAMEDIAITEM VALUES (?,?,?,?,?,?,?)", [
        (1, 6, None, "Media/x/factura.pdf", "https://mmg.whatsapp.net/f", b"KEY", None),
        (2, 10, "foto de la pizarra", None, "https://mmg.whatsapp.net/i", b"KEY", None),
        (3, 3, None, None, None, None, quote),
    ])
    db.executemany("INSERT INTO ZWAGROUPMEMBER VALUES (?,?,?,?,?)", [
        (1, 1, ANA_LID, None, 1), (2, 3, "34600000002@s.whatsapp.net", "Mamá", 1),
        (3, 6, ANA_LID, None, 1), (4, 7, ANA_LID, None, 1),
    ])
    db.execute("INSERT INTO ZWAPROFILEPUSHNAME VALUES (1, ?, 'Ana (push)')", (ANA_LID,))
    db.commit()

    contacts = sqlite3.connect(root / "ContactsV2.sqlite")
    contacts.executescript("""
        CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZFULLNAME TEXT, ZWHATSAPPID TEXT,
            ZLID TEXT, ZNOTES TEXT);
        INSERT INTO ZWAADDRESSBOOKCONTACT VALUES (1, 'Ana López', '34600000001@s.whatsapp.net', NULL, 'nota privada');
    """)
    contacts.close()
    lid = sqlite3.connect(root / "LID.sqlite")
    lid.executescript("""
        CREATE TABLE ZWAZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZPHONENUMBER TEXT, ZDISPLAYNAME TEXT);
        INSERT INTO ZWAZACCOUNT VALUES (1, '900@lid', '+34600000001', NULL);
        INSERT INTO ZWAZACCOUNT VALUES (2, '555@lid', '+34 600 000 005', NULL);
    """)
    lid.close()
    return db  # kept open so ChatStorage keeps its -wal and -shm, like WhatsApp does


POLICY = """
[content]
chats = ["Grupo Proyecto", "Ana Investigadora", "+34 600 000 005", "Chat que no existe"]
people = ["Ana Investigadora"]

[exclude]
chats = ["Grupo excluido con Ana"]

[options]
days = 100000
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


@pytest.fixture(scope="session")
def helper_binary():
    if not shutil.which("swiftc"):
        pytest.skip("swiftc not available (install the Xcode Command Line Tools)")
    build = subprocess.run(["make", "build"], cwd=REPO, capture_output=True, text=True)
    if build.returncode:
        pytest.fail(f"make build failed:\n{build.stdout}\n{build.stderr}")
    return BINARY


@pytest.fixture
def env(tmp_path, monkeypatch, helper_binary):
    source = tmp_path / "container"
    writer = build_container(source)
    data = tmp_path / "data"
    policy = tmp_path / "policy.toml"
    policy.write_text(POLICY)
    monkeypatch.setenv("WA_SOURCE_DIR", str(source))
    monkeypatch.setenv("WA_DATA_DIR", str(data))
    monkeypatch.setenv("WA_POLICY", str(policy))
    monkeypatch.setenv("WA_HELPER_BIN", str(BINARY))
    monkeypatch.setattr(exporter, "caller_can_read_whatsapp", lambda: False)  # never touch the real container
    yield {"source": source, "data": data, "policy": policy, "writer": writer}
    writer.close()
