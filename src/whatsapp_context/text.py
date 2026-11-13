"""Dates, text sanitising and the tiny protobuf reader used for quoted replies."""

import os
import re
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CORE_DATA_EPOCH = 978307200  # 2001-01-01 UTC in Unix seconds


def _local_zone() -> tzinfo:
    """WA_TZ or TZ if set, else the Mac's zone (/etc/localtime), so DST is handled by name."""
    link = os.path.realpath("/etc/localtime")
    mac = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else None
    for name in filter(None, (os.environ.get("WA_TZ"), os.environ.get("TZ"), mac)):
        try:
            return ZoneInfo(name.lstrip(":"))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo or UTC


LOCAL = _local_zone()

# C0/C1 controls (keeping \t and \n), invisible formatting chars, bidi overrides/isolates and
# Unicode tag characters: they can hide or reorder text shown to the model or the terminal.
# ZWJ (U+200D) stays because composed emoji need it. Built from code points so the source
# file itself contains no invisible characters.
_UNSAFE_RANGES = [(0x00, 0x08), (0x0B, 0x1F), (0x7F, 0x9F), (0x061C, 0x061C), (0x180E, 0x180E),
                  (0x200B, 0x200C), (0x200E, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064),
                  (0x2066, 0x206F), (0xFEFF, 0xFEFF), (0xE0000, 0xE007F)]
_UNSAFE = re.compile("[" + "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in _UNSAFE_RANGES) + "]")
_LINE_SEPARATORS = re.compile("[" + "".join(map(chr, (0x0D, 0x2028, 0x2029))) + "]")


def clean(text: str | None) -> str:
    """Remove control, invisible and bidi characters; turn line separators into newlines."""
    return _UNSAFE.sub("", _LINE_SEPARATORS.sub("\n", text)) if text else ""


def from_coredata(seconds: float | None) -> datetime | None:
    """Convert Core Data seconds to an aware datetime in the local zone."""
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds + CORE_DATA_EPOCH, UTC).astimezone(LOCAL)


def to_coredata(moment: datetime) -> float:
    """Convert an aware datetime to Core Data seconds."""
    return moment.timestamp() - CORE_DATA_EPOCH


def fmt(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "-"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def parse_local(value: str | None) -> datetime | None:
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' as local time."""
    if not value:
        return None
    moment = datetime.fromisoformat(value.strip())
    return moment.replace(tzinfo=LOCAL) if moment.tzinfo is None else moment


def parse_until(value: str | None) -> datetime | None:
    """Exclusive upper bound: a bare date ('YYYY-MM-DD') includes that whole day."""
    moment = parse_local(value)
    if moment and len(value.strip()) == 10:
        moment += timedelta(days=1)
    return moment


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, i
        shift += 7
    raise ValueError("truncated varint")


def protobuf_fields(buf: bytes) -> dict[int, list]:
    """Schema-less parse of one protobuf level: varints and length-delimited fields."""
    fields: dict[int, list] = {}
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            value, i = buf[i:i + size], i + size
        elif wire == 1:
            value, i = None, i + 8
        elif wire == 5:
            value, i = None, i + 4
        else:
            raise ValueError(f"wire type {wire}")
        fields.setdefault(number, []).append(value)
    return fields


def quoted_reply(metadata: bytes | None) -> tuple[str, str, str] | None:
    """Return (stanza_id, sender_jid, text) of the quoted message, if any.

    Layout of ZWAMEDIAITEM.ZMETADATA documented by jonathanhowell/whatsapp-mcp
    (docs/SCHEMA.md, MIT): field 5 quoted stanza id, 6 sender JID, 19.1 text.
    """
    def first(fields: dict[int, list], number: int) -> bytes:
        value = fields.get(number, [b""])[0]
        return value if isinstance(value, bytes) else b""
    try:
        fields = protobuf_fields(bytes(metadata or b""))
        stanza, quoted = first(fields, 5), first(fields, 19)
        if not stanza:
            return None
        text = first(protobuf_fields(quoted), 1) if quoted else b""
        return stanza.decode(errors="replace"), first(fields, 6).decode(errors="replace"), text.decode(errors="replace")
    except (ValueError, IndexError, TypeError):
        return None
