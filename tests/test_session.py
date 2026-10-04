import asyncio
import math
import struct

import pytest

import storage
from config import get_settings
from engines.llm import FakeLLM, RulesLLM
from engines.stt import FakeSTT, SttResult
from engines.translate import FakeTranslator
from session_manager import Engines, SuggestionFilter, TourHub


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)

    def of(self, kind):
        return [m for m in self.sent if m["type"] == kind]


def make_hub(stt=None, llm=None, llm_available=False):
    engines = Engines(stt=stt or FakeSTT(), translator=FakeTranslator(), llm=llm or RulesLLM(), llm_available=llm_available)
    return TourHub(get_settings(), engines)


async def settle(session, rounds=40):
    for _ in range(rounds):
        await asyncio.sleep(0.05)
        if session.queue.empty() and (session._coach_task is None or session._coach_task.done()):
            return


def run(coro):
    return asyncio.run(coro)


def test_filter_blocks_duplicates_and_no_update():
    f = SuggestionFilter(min_publish_interval_s=0)
    assert not f.filter("NO_UPDATE").accepted
    assert f.filter("The oldest trees are over 300 years old.").accepted
    assert not f.filter("The oldest trees are over 300 years old.").accepted
    assert not f.filter("As an AI, I think...").accepted


def test_guest_question_is_translated_tagged_and_coached():
    async def scenario():
        hub = make_hub()
        guide, guest = FakeSocket(), FakeSocket()
        hub.add(guide, "guide")
        hub.add(guest, "guest")
        session = await hub.start({"tour_type": "farm_tour", "guide_lang": "en"})
        await session.handle_message(hub.clients[id(guide)], {"type": "set_stop", "stop": "grove"})
        await session.process_turn("Quel âge ont les arbres ?", "fr")
        await settle(session)
        return session, guide, guest

    session, guide, guest = run(scenario())
    entry = guide.of("transcript")[-1]["entry"]
    assert entry["speaker"] == "guest" and entry["lang"] == "fr"
    assert entry["text_guide"] == "[en] Quel âge ont les arbres ?"
    assert entry["tags"]["question"] and entry["tags"]["stop"] == "grove"
    assert session.guest_lang == "fr"
    suggestion = guide.of("suggestion")[-1]
    assert suggestion["guest_lang"] == "fr" and suggestion["say_guest"].startswith("[fr] ")
    # Coaching is private to the guide.
    assert not guest.of("suggestion")
    assert guest.of("transcript")


def test_guide_line_goes_to_guests_in_every_language():
    async def scenario():
        hub = make_hub()
        guest = FakeSocket()
        hub.add(FakeSocket(), "guide")
        hub.add(guest, "guest")
        session = await hub.start({"guide_lang": "en"})
        await session.process_turn("Bonjour", "fr")
        await session.process_turn("Hallo", "de")
        await session.process_turn("Welcome to the press.", "en")
        await settle(session)
        return guest

    guest = run(scenario())
    line = guest.of("guest_line")[-1]
    assert line["lines"]["fr"]["text"] == "[fr] Welcome to the press."
    assert line["lines"]["de"]["bcp47"] == "de-DE"
    assert line["lang"] == "de"


def test_blink_text_from_guest_screen_feeds_the_coach():
    async def scenario():
        hub = make_hub()
        guide, guest = FakeSocket(), FakeSocket()
        hub.add(guide, "guide")
        guest_client = hub.add(guest, "guest")
        session = await hub.start({"guide_lang": "en"})
        await session.handle_message(guest_client, {"type": "set_stop", "stop": "press"})
        await session.handle_message(guest_client, {"type": "guest_text", "source": "blink", "text": "Can I taste it?"})
        await settle(session)
        return session, guide

    session, guide = run(scenario())
    entry = guide.of("transcript")[-1]["entry"]
    assert entry["source"] == "blink" and entry["speaker"] == "guest"
    assert session.engagement.blink_turns == 1
    assert guide.of("suggestion")


def test_say_to_guest_records_a_guide_turn_and_marks_suggestion_used():
    llm = FakeLLM([{"say": "About five kilos of olives make one litre of oil.", "topic": "olive_oil"}])

    async def scenario():
        hub = make_hub(llm=llm, llm_available=True)
        guide, guest = FakeSocket(), FakeSocket()
        guide_client = hub.add(guide, "guide")
        hub.add(guest, "guest")
        session = await hub.start({"guide_lang": "en"})
        await session.process_turn("Combien d'olives pour un litre ?", "fr")
        await settle(session)
        s = guide.of("suggestion")[-1]
        await session.handle_message(guide_client, {"type": "say_to_guest", "text": s["say"]})
        await settle(session)
        return session, guest

    session, guest = run(scenario())
    assert session.suggestions[0]["used"] is True
    assert session.turns[-1]["source"] == "copilot"
    assert guest.of("guest_line")[-1]["lines"]["fr"]["text"].startswith("[fr] About five kilos")


def tone(ms, amp=8000):
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(16 * ms))


def test_audio_goes_through_vad_and_stt():
    stt = FakeSTT([SttResult("Wann ist die Ernte?", "de", 0.97, 1200)])

    async def scenario():
        hub = make_hub(stt=stt)
        guide = FakeSocket()
        client = hub.add(guide, "guide")
        session = await hub.start({"guide_lang": "en"})
        audio = b"\x00\x00" * 16 * 800 + tone(1200) + b"\x00\x00" * 16 * 1000
        for i in range(0, len(audio), 1280):
            await session.handle_audio(client, audio[i : i + 1280])
        await settle(session)
        return session, guide

    session, guide = run(scenario())
    assert any(m["active"] for m in guide.of("listening"))
    entry = guide.of("transcript")[-1]["entry"]
    assert entry["text"] == "Wann ist die Ernte?" and entry["speaker"] == "guest"


def test_stop_saves_tour_with_report():
    async def scenario():
        hub = make_hub()
        guide = FakeSocket()
        hub.add(guide, "guide")
        session = await hub.start({"guide_lang": "en"})
        await session.handle_message(hub.clients[id(guide)], {"type": "set_stop", "stop": "press"})
        await session.process_turn("Is the oil organic?", "fr")
        await session.process_turn("I'm not sure, I'll check.", "en")
        await settle(session)
        await session.stop()
        return session, guide

    session, guide = run(scenario())
    saved = storage.get_tour(session.tour_id)
    assert saved and saved["report"]["question_count"] == 1
    assert saved["report"]["unanswered"] == ["[en] Is the oil organic?"]
    assert guide.of("tour_ended")
    assert storage.list_tours()[0]["id"] == session.tour_id


def test_guest_cannot_start_or_end_tours():
    from app import _handle_text

    async def scenario():
        hub = make_hub()
        guest = FakeSocket()
        client = hub.add(guest, "guest")
        await _handle_text(hub, client, '{"type": "start"}')
        return hub

    assert run(scenario()).session is None


class EchoSTT:
    """Answers partials and finals alike, like the real model."""

    ready = True

    def transcribe(self, pcm, hint=None, language=None):
        words = ["Wann", "ist", "die", "Ernte?"]
        n = max(1, min(4, len(pcm) // 16000))
        return SttResult(" ".join(words[:n]), "de", 0.95, len(pcm) // 32)


def test_live_captions_then_final_replaces_them():
    async def scenario():
        hub = make_hub(stt=EchoSTT())
        guide = FakeSocket()
        client = hub.add(guide, "guide")
        session = await hub.start({"guide_lang": "en"})
        audio = b"\x00\x00" * 16 * 800 + tone(2500) + b"\x00\x00" * 16 * 1000
        for i in range(0, len(audio), 1280):
            await session.handle_audio(client, audio[i : i + 1280])
            await asyncio.sleep(0.02)
        await settle(session)
        return guide

    guide = run(scenario())
    partials = guide.of("partial")
    assert any(not p.get("final") for p in partials), "no live caption while speaking"
    assert partials[-1]["final"] and partials[-1]["text"] == "Wann ist die Ernte?"
    entry = guide.of("transcript")[-1]["entry"]
    assert entry["utt"] == partials[-1]["utt"]
    assert entry["latency_ms"] is not None
