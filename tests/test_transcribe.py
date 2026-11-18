"""Voice-note transcription: WAExport copies only allowed notes, only text is kept, and it is pruned."""

import json
import os
import platform
import stat
import subprocess

import pytest
from conftest import REPO, cd

from whatsapp_context import exporter, transcripts
from whatsapp_context.store import Store, connect

FAKE_TRANSCRIBER = """#!/bin/sh
locale="$1"; shift
for f in "$@"; do
  test -f "$f" && echo "$f" >> "$(dirname "$0")/seen"
  printf '{"file":"%s","seconds":23,"locale":"%s","text":"nota de voz %s","confidence":0.9}\\n' \\
    "$(basename "$f")" "$locale" "$(basename "$f")"
done
"""


def voice(env, pk: int, chat: int, path: str | None, seconds: float = 23, content: bytes | None = b"OggS fake"):
    """A voice note (ZMESSAGETYPE 3) with its media row and, when `content` is given, its file on disk."""
    env["writer"].execute("INSERT INTO ZWAMEDIAITEM VALUES (?, ?, NULL, ?, NULL, NULL, NULL, ?)",
                          (pk + 100, pk, path, seconds))
    env["writer"].execute("INSERT INTO ZWAMESSAGE VALUES (?, ?, ?, 0, 3, NULL, NULL, NULL, ?, ?)",
                          (pk, chat, cd(2026, 10, 21, 9, pk % 60), pk + 100, f"V{pk}"))
    env["writer"].commit()
    if path and content is not None:
        file = env["source"] / "Message" / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    return pk


@pytest.fixture
def voice_env(env, tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "wa-transcribe"
    fake.parent.mkdir()
    fake.write_text(FAKE_TRANSCRIBER)
    fake.chmod(0o755)
    monkeypatch.setenv("WA_TRANSCRIBER", str(fake))
    env["policy"].write_text(env["policy"].read_text() + 'transcribe = "es-ES"\n')
    env["seen"] = fake.parent / "seen"
    return env


def test_helper_copies_only_allowed_voice_notes(env):
    ok = voice(env, 20, 1, "Media/111/nota.opus")
    other_chat = voice(env, 21, 3, "Media/222/nota.opus")  # Familia: metadata only
    excluded = voice(env, 22, 7, "Media/444/nota.opus")
    escape = voice(env, 23, 1, "../ChatStorage.sqlite", content=None)
    link = voice(env, 24, 1, "Media/111/link.opus", content=None)
    (env["source"] / "Message" / "Media" / "111" / "link.opus").symlink_to(env["source"] / "ChatStorage.sqlite")
    pdf = voice(env, 25, 1, "Media/111/nota.pdf")
    missing = voice(env, 26, 1, "Media/111/missing.opus", content=None)
    big = voice(env, 27, 1, "Media/111/big.opus", content=b"")
    os.truncate(env["source"] / "Message" / "Media" / "111" / "big.opus", 17 << 20)
    exporter.refresh()
    result = exporter.copy_audio({pk: f"V{pk}" for pk in [ok, other_chat, excluded, escape, link, pdf, missing, big]})
    assert result["ok"] and [c["pk"] for c in result["copied"]] == [ok]
    reasons = {s["pk"]: s["reason"] for s in result["skipped"]}
    assert reasons[other_chat] == reasons[excluded] == "not an allowed voice note"
    assert reasons[escape] == reasons[pdf] == "unsupported file"
    assert reasons[missing] == "not downloaded" and reasons[link] in ("unreadable", "not downloaded")
    assert reasons[big] == "too large"
    audio = env["data"] / "audio"
    assert sorted(p.name for p in audio.iterdir()) == ["20.opus"]
    assert stat.S_IMODE((audio / "20.opus").stat().st_mode) == 0o600 and stat.S_IMODE(audio.stat().st_mode) == 0o700
    assert not (env["data"] / "audio-request.json").exists()


def test_transcription_is_off_until_the_policy_names_a_language(env):
    voice(env, 20, 1, "Media/111/nota.opus")
    with pytest.raises(ValueError, match="Transcription is off"):
        exporter.transcribe("Grupo Proyecto")


def test_only_text_is_kept_and_audio_copies_are_deleted(voice_env):
    voice(voice_env, 20, 1, "Media/111/nota.opus", seconds=83)
    voice(voice_env, 26, 1, "Media/111/missing.opus", content=None)
    out = exporter.transcribe("Grupo Proyecto")
    assert "Transcribed 1 voice note on this Mac (es-ES)" in out and "1 voice note not downloaded" in out
    assert "[audio 1:23 · transcribed on this Mac, es-ES, may contain errors] nota de voz 20.opus" in out
    assert "[audio 0:23 · not transcribed]" in out  # WhatsApp lists a path, but the file is gone
    assert voice_env["seen"].read_text().strip().endswith("audio/20.opus")  # the copy existed while transcribing
    assert list((voice_env["data"] / "audio").iterdir()) == []
    assert stat.S_IMODE(transcripts.cache_file().stat().st_mode) == 0o600
    again = exporter.transcribe("Grupo Proyecto")
    assert "Transcribed 0 voice notes" in again and "1 already had a transcript" in again
    assert voice_env["seen"].read_text().count("20.opus") == 1  # cached: never copied twice


def test_search_finds_words_inside_transcripts(voice_env):
    voice(voice_env, 20, 1, "Media/111/nota.opus")
    exporter.transcribe("Grupo Proyecto")
    with connect() as conn:
        assert "nota de voz 20.opus" in Store(conn).search("nota de voz")


def test_excluding_a_chat_prunes_its_transcripts(voice_env):
    voice(voice_env, 20, 1, "Media/111/nota.opus")
    exporter.transcribe("Grupo Proyecto")
    assert set(transcripts.load()) == {20}
    policy = voice_env["policy"].read_text().replace('chats = ["Grupo excluido con Ana"]',
                                                     'chats = ["Grupo excluido con Ana", "Grupo Proyecto"]')
    voice_env["policy"].write_text(policy)
    assert exporter.refresh()["ok"]
    assert transcripts.load() == {}


def test_transcriber_errors_are_reported_and_copies_still_deleted(voice_env, tmp_path, monkeypatch):
    broken = tmp_path / "bin" / "broken"
    broken.write_text('#!/bin/sh\necho \'{"error":"speech model for es_ES is not installed"}\'\n')
    broken.chmod(0o755)
    monkeypatch.setenv("WA_TRANSCRIBER", str(broken))
    voice(voice_env, 20, 1, "Media/111/nota.opus")
    with pytest.raises(exporter.ExportError, match="not installed"):
        exporter.transcribe("Grupo Proyecto")
    assert list((voice_env["data"] / "audio").iterdir()) == []


def test_transcribe_option_must_be_a_language(env):
    env["policy"].write_text(env["policy"].read_text() + "transcribe = 5\n")
    assert not exporter.refresh()["ok"]


@pytest.mark.skipif(int(platform.mac_ver()[0].split(".")[0] or 0) < 26, reason="Apple SpeechTranscriber needs macOS 26")
def test_apple_engine_transcribes_synthetic_spanish(tmp_path, helper_binary):
    if not (REPO / "build" / "wa-transcribe").exists():
        pytest.skip("built without the macOS 26 SDK")
    aiff, m4a = tmp_path / "es.aiff", tmp_path / "es.m4a"
    spoken = subprocess.run(["say", "-v", "Monica", "-o", str(aiff), "Hola, confirmo la reunión del jueves."])
    if spoken.returncode:
        pytest.skip("the Spanish system voice is not installed")
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(m4a)], check=True)
    proc = subprocess.run([str(REPO / "build" / "wa-transcribe"), "es-ES", str(m4a)], capture_output=True, text=True,
                          timeout=120)
    item = json.loads(proc.stdout.splitlines()[-1])
    if "not installed" in item.get("error", "") or "not available" in item.get("error", ""):
        pytest.skip(item["error"])
    assert "reunión" in item["text"].lower()  # synthetic voices score low confidence; the words are what count


def test_helper_never_writes_through_a_swapped_audio_folder(env, tmp_path):
    ok = voice(env, 20, 1, "Media/111/nota.opus")
    exporter.refresh()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (env["data"] / "audio").symlink_to(elsewhere)
    result = exporter.copy_audio({ok: f"V{ok}"})
    assert not result["ok"] and list(elsewhere.iterdir()) == []


def test_helper_rejects_notes_reached_through_a_linked_folder_or_fifo(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "nota.opus").write_bytes(b"OggS outside")
    linked = voice(env, 20, 1, "Media/333/nota.opus", content=None)
    (env["source"] / "Message" / "Media").mkdir(parents=True, exist_ok=True)
    (env["source"] / "Message" / "Media" / "333").symlink_to(outside)
    fifo = voice(env, 21, 1, "Media/111/fifo.opus", content=None)
    (env["source"] / "Message" / "Media" / "111").mkdir(parents=True, exist_ok=True)
    os.mkfifo(env["source"] / "Message" / "Media" / "111" / "fifo.opus")
    exporter.refresh()
    result = exporter.copy_audio({linked: f"V{linked}", fifo: f"V{fifo}"})  # the FIFO does not block it
    assert result["ok"] and result["copied"] == []
    assert list((env["data"] / "audio").iterdir()) == []


def test_a_revoke_during_transcription_wins(voice_env, monkeypatch):
    voice(voice_env, 20, 1, "Media/111/nota.opus")
    real = exporter._transcriber_lines

    def revoke_meanwhile(args, timeout):
        items = real(args, timeout)
        policy = voice_env["policy"].read_text().replace('chats = ["Grupo excluido con Ana"]',
                                                         'chats = ["Grupo excluido con Ana", "Grupo Proyecto"]')
        voice_env["policy"].write_text(policy)
        assert exporter.refresh()["ok"]
        return items

    monkeypatch.setattr(exporter, "_transcriber_lines", revoke_meanwhile)
    with pytest.raises(LookupError):  # the chat is gone by the time the answer is rendered
        exporter.transcribe("Grupo Proyecto")
    assert transcripts.load() == {}


def test_a_reused_message_id_does_not_inherit_a_transcript(voice_env):
    voice(voice_env, 20, 1, "Media/111/nota.opus")
    exporter.transcribe("Grupo Proyecto")
    voice_env["writer"].execute("UPDATE ZWAMESSAGE SET ZSTANZAID = 'OTHER' WHERE Z_PK = 20")  # a restored database
    voice_env["writer"].commit()
    assert exporter.refresh()["ok"]
    assert transcripts.load() == {}
    with connect() as conn:
        assert "[audio 0:23 · not transcribed]" in Store(conn).messages("Grupo Proyecto")


def test_the_helper_checks_the_stanza_id_too(voice_env):
    voice(voice_env, 20, 1, "Media/111/nota.opus")
    exporter.refresh()  # the export still knows message 20 as V20
    voice_env["writer"].execute("UPDATE ZWAMESSAGE SET ZSTANZAID = 'OTHER' WHERE Z_PK = 20")
    voice_env["writer"].commit()
    result = exporter.copy_audio({20: "V20"})
    assert result["copied"] == [] and result["skipped"][0]["reason"] == "not an allowed voice note"
