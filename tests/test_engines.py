import math
import struct

import languages
from engines.llm import parse_json
from engines.translate import FakeTranslator, _split_sentences
from engines.vad import EnergyVad


def tone(ms, amp=8000, freq=220):
    n = 16 * ms
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / 16000))) for i in range(n))


def silence(ms):
    return b"\x00\x00" * 16 * ms


def test_language_maps():
    assert languages.normalize("fr-FR") == "fr"
    assert languages.normalize("fra_Latn") == "fr"
    assert languages.normalize("xx") is None
    assert languages.flores("ar") == "arb_Arab"
    assert languages.bcp47("de") == "de-DE"
    assert languages.is_rtl("ar") and not languages.is_rtl("fr")


def test_vad_cuts_one_utterance_from_speech_between_silences():
    vad = EnergyVad()
    events = vad.push(silence(1000))
    events += vad.push(tone(1200))
    events += vad.push(silence(1000))
    kinds = [e.kind for e in events]
    assert kinds == ["speech_start", "utterance"]
    utterance = events[-1]
    assert 1200 <= utterance.duration_ms <= 2200


def test_vad_ignores_clicks_and_flushes_tail():
    vad = EnergyVad()
    events = vad.push(silence(500) + tone(60) + silence(800))
    assert not [e for e in events if e.kind == "utterance"]
    vad.push(silence(300) + tone(900))
    flushed = vad.flush()
    assert flushed and flushed[0].kind == "utterance"


def test_vad_handles_odd_chunk_sizes():
    vad = EnergyVad()
    stream = silence(600) + tone(1000) + silence(1000)
    events = []
    for i in range(0, len(stream), 1234):
        events += vad.push(stream[i : i + 1234])
    assert [e.kind for e in events] == ["speech_start", "utterance"]


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"say": "hi"}\n```') == {"say": "hi"}
    assert parse_json("nope") is None
    assert parse_json("[1, 2]") is None


def test_sentence_split_and_fake_translator():
    assert _split_sentences("Hello there. How are you? Fine") == ["Hello there.", "How are you?", "Fine"]
    t = FakeTranslator()
    assert t.translate("Bonjour", "fr", "en") == "[en] Bonjour"
    assert t.translate("Hello", "en", "en-US") == "Hello"
