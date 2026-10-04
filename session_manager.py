"""Live tour session. Forked from orbit.ai's TeleprompterSession.

orbit.ai streamed one buyer's audio to Gemini Live and got back suggestions.
Here every step runs on the device: an energy VAD cuts the stream into
utterances, Whisper transcribes and identifies the language, NLLB translates,
the engagement tracker credits questions to the current stop, and the coach
writes Noor's next line. One guide console and any number of guest screens
share a tour through the TourHub.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from starlette.websockets import WebSocket, WebSocketDisconnect
except ImportError:  # tests without starlette
    WebSocket = Any

    class WebSocketDisconnect(Exception):
        pass

import languages
import storage
from coach import Coach, CoachResult, Turn
from config import DATA_DIR, Settings, TOUR_TYPES, normalize_tour_type
from engagement import TourEngagement
from engines.llm import LLM
from engines.stt import SpeechToText
from engines.translate import Translator
from engines.vad import EnergyVad

# Floor between two published suggestions (orbit.ai used 0.4 s for a stream of
# tokens; tour turns are whole utterances, so a short floor is enough).
MIN_PUBLISH_INTERVAL_S = 0.4
# Consecutive visitor turns that land this close together are answered as one.
COACH_DEBOUNCE_S = 0.35
COACH_TIMEOUT_S = 14.0
# Live captions: re-transcribe the turn in progress this often while someone
# is speaking. A partial costs ~0.15 s of CPU with the short-window encoder.
PARTIAL_INTERVAL_S = 0.5
PARTIAL_MIN_SPEECH_MS = 500
DEMO_SCRIPT = DATA_DIR / "demo_tour_script.json"


def utc_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class FilterDecision:
    accepted: bool
    text: str = ""
    reason: str = ""


class SuggestionFilter:
    """Unchanged from orbit.ai apart from the meta phrases a tour coach leaks."""

    META_PATTERNS = [
        re.compile(r"\bas an ai\b", re.I),
        re.compile(r"\bhere(?:'s| is) (?:a|the) suggestion\b", re.I),
        re.compile(r"\bnoor should say\b", re.I),
        re.compile(r"\bthe guide should say\b", re.I),
        re.compile(r"\bsystem prompt\b", re.I),
        re.compile(r"\bcoaching notes?\b", re.I),
    ]

    def __init__(self, min_publish_interval_s: float = MIN_PUBLISH_INTERVAL_S):
        self.min_publish_interval_s = min_publish_interval_s
        self.last_published: str = ""
        self.last_publish_time = 0.0

    def filter(self, candidate: str | None, now: float | None = None) -> FilterDecision:
        now = time.monotonic() if now is None else now
        text = (candidate or "").strip()
        if not text:
            return FilterDecision(False, reason="empty")
        if text.upper() == "NO_UPDATE":
            return FilterDecision(False, reason="no_update")
        if any(pattern.search(text) for pattern in self.META_PATTERNS):
            return FilterDecision(False, reason="meta_response")
        if self.last_published:
            if _normalize(text) == _normalize(self.last_published):
                return FilterDecision(False, reason="duplicate")
            if _similarity(text, self.last_published) >= 0.82:
                return FilterDecision(False, reason="near_duplicate")
        if self.last_publish_time and now - self.last_publish_time < self.min_publish_interval_s:
            return FilterDecision(False, reason="rate_limited")
        self.last_published = text
        self.last_publish_time = now
        return FilterDecision(True, text=text, reason="published")


class JsonlLogger:
    def __init__(self, enabled: bool, log_dir: Path):
        self.enabled = enabled
        self.path: Path | None = None
        if enabled:
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            self.path = log_dir / f"tour_{stamp}.jsonl"

    def write(self, event: str, **data: Any) -> None:
        if not self.enabled or self.path is None:
            return
        data["event"] = event
        data["ts"] = datetime.now(UTC).isoformat()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


@dataclass
class Engines:
    stt: SpeechToText
    translator: Translator
    llm: LLM
    llm_available: bool = False


@dataclass
class Client:
    websocket: Any
    role: str  # "guide" | "guest"
    vad: EnergyVad = field(default_factory=EnergyVad)
    utt_seq: int = 0
    partial_busy: bool = False
    last_partial_at: float = 0.0
    partial_lang: str | None = None

    @property
    def utt_id(self) -> str:
        return f"{self.role}-{id(self.websocket) % 10000}-{self.utt_seq}"


class TourHub:
    """All connected screens, and the tour they share (one at a time: Noor
    runs one group at a time)."""

    def __init__(self, settings: Settings, engines: Engines):
        self.settings = settings
        self.engines = engines
        self.clients: dict[int, Client] = {}
        self.session: TourSession | None = None
        self.stt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        self.mt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt")

    def add(self, websocket: Any, role: str) -> Client:
        client = Client(websocket, "guest" if role == "guest" else "guide")
        self.clients[id(websocket)] = client
        return client

    def remove(self, websocket: Any) -> None:
        self.clients.pop(id(websocket), None)

    async def broadcast(self, message_type: str, roles: set[str] | None = None, **payload: Any) -> None:
        message = {"type": message_type, **payload}
        for client in list(self.clients.values()):
            if roles is not None and client.role not in roles:
                continue
            try:
                await client.websocket.send_json(message)
            except (WebSocketDisconnect, RuntimeError):
                self.remove(client.websocket)
            except Exception:
                self.remove(client.websocket)

    async def send(self, websocket: Any, message_type: str, **payload: Any) -> None:
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": message_type, **payload})

    def state(self) -> dict[str, Any]:
        if self.session is not None and self.session.running:
            return self.session.state()
        profile = storage.get_profile()
        return {
            "running": False,
            "tour_id": None,
            "guide_lang": profile.get("guide_language", "en"),
            "guest_lang": None,
            "stops": [{"id": s.get("id"), "name": s.get("name")} for s in profile.get("stops", [])],
            "tour_types": TOUR_TYPES,
        }

    async def start(self, message: dict[str, Any]) -> "TourSession":
        if self.session is not None and self.session.running:
            return self.session
        profile = storage.get_profile()
        session = TourSession(self, profile)
        self.session = session
        await session.start(
            tour_type=message.get("tour_type"),
            guide_lang=message.get("guide_lang") or profile.get("guide_language"),
            guest_lang=message.get("guest_lang"),
        )
        return session


class TourSession:
    def __init__(self, hub: TourHub, profile: dict[str, Any]):
        self.hub = hub
        self.settings = hub.settings
        self.engines = hub.engines
        self.profile = profile
        self.tour_id = storage.new_tour_id()
        self.tour_type = "farm_tour"
        self.guide_lang = languages.normalize(profile.get("guide_language")) or "en"
        self.guest_lang: str | None = None
        self.guest_lang_pinned = False
        self.guest_languages: list[str] = []
        self.speaker_mode = "auto"  # "auto" | "guide" | "guest"
        self.running = False
        self.demo = False
        self.started_at: str | None = None
        self.turns: list[dict[str, Any]] = []
        self.suggestions: list[dict[str, Any]] = []
        self.engagement = TourEngagement.from_profile(profile)
        self.coach = Coach(self.engines.llm, profile, self.tour_type)
        self.filter = SuggestionFilter()
        self.logger = JsonlLogger(self.settings.debug, self.settings.log_dir)
        self.queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue(maxsize=50)
        self.tasks: set[asyncio.Task[Any]] = set()
        self._coach_task: asyncio.Task[Any] | None = None
        self._demo_task: asyncio.Task[Any] | None = None
        self._turn_seq = 0
        self.stt_latencies: list[int] = []
        self.coach_latencies: list[int] = []

    # ---------- Lifecycle ----------

    async def start(self, tour_type: str | None, guide_lang: str | None, guest_lang: str | None = None) -> None:
        self.tour_type = normalize_tour_type(tour_type)
        self.coach = Coach(self.engines.llm, self.profile, self.tour_type)
        self.guide_lang = languages.normalize(guide_lang) or self.guide_lang
        pinned = languages.normalize(guest_lang)
        if pinned:
            self.guest_lang = pinned
            self.guest_lang_pinned = True
            self._note_guest_language(pinned)
        self.running = True
        self.started_at = datetime.now(UTC).isoformat()
        self.tasks.add(asyncio.create_task(self._worker(), name="tour-worker"))
        self.logger.write("tour_start", tour_id=self.tour_id, tour_type=self.tour_type)
        await self.hub.broadcast("tour_state", **self.state())
        llm = "on-device model" if self.engines.llm_available else "built-in FAQ rules"
        await self._status("ok", f"Tour started. Suggestions from the {llm}.")

    def state(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "tour_id": self.tour_id,
            "tour_type": self.tour_type,
            "tour_types": TOUR_TYPES,
            "guide_lang": self.guide_lang,
            "guest_lang": self.guest_lang,
            "guest_lang_pinned": self.guest_lang_pinned,
            "guest_lang_rtl": languages.is_rtl(self.guest_lang),
            "guest_bcp47": languages.bcp47(self.guest_lang) if self.guest_lang else None,
            "speaker_mode": self.speaker_mode,
            "current_stop": self.engagement.current_stop,
            "stops": self.engagement.stops,
            "demo": self.demo,
        }

    async def stop(self) -> dict[str, Any]:
        if not self.running:
            return {}
        # Speech still inside a VAD buffer is the end of someone's sentence.
        for client in list(self.hub.clients.values()):
            for event in client.vad.flush():
                if event.kind == "utterance":
                    with contextlib.suppress(asyncio.QueueFull):
                        self.queue.put_nowait(("audio", (event.pcm, client.role)))
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)
        if self._demo_task is not None:
            self._demo_task.cancel()
        workers = [t for t in self.tasks if t.get_name() == "tour-worker"]
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), timeout=30)
        if self._coach_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self._coach_task, timeout=COACH_TIMEOUT_S)
        for task in list(self.tasks):
            task.cancel()
        self.running = False
        tour = self.to_record()
        from insights import build_tour_report

        tour["report"] = build_tour_report(tour, self.profile)
        try:
            await asyncio.to_thread(storage.save_tour, tour)
        except Exception as exc:
            self.logger.write("save_failed", error=str(exc))
        self.logger.write("tour_stop", tour_id=self.tour_id)
        await self.hub.broadcast("tour_ended", tour_id=self.tour_id, report=tour["report"])
        await self.hub.broadcast("tour_state", **self.state())
        return tour

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.tour_id,
            "tour_type": self.tour_type,
            "started_at": self.started_at,
            "ended_at": datetime.now(UTC).isoformat(),
            "guide_lang": self.guide_lang,
            "guest_languages": self.guest_languages,
            "demo": self.demo,
            "turns": self.turns,
            "suggestions": self.suggestions,
            "engagement": self.engagement.snapshot(),
            "latency": {
                "stt_avg_ms": _avg(self.stt_latencies),
                "coach_avg_ms": _avg(self.coach_latencies),
            },
        }

    # ---------- Inputs ----------

    async def handle_audio(self, client: Client, pcm: bytes) -> None:
        if not self.running:
            return
        for event in client.vad.push(pcm):
            if event.kind == "speech_start":
                client.utt_seq += 1
                client.partial_lang = None
                await self.hub.broadcast("listening", active=True, role=client.role, utt=client.utt_id)
            elif event.kind == "utterance":
                try:
                    self.queue.put_nowait(("audio", (event.pcm, client.role, client.utt_id, time.monotonic(), client.partial_lang)))
                except asyncio.QueueFull:
                    await self._status("warning", "Speech is arriving faster than it can be transcribed.")
        now = time.monotonic()
        if (
            client.vad.in_speech
            and not client.partial_busy
            and client.vad.speech_ms >= PARTIAL_MIN_SPEECH_MS
            and now - client.last_partial_at >= PARTIAL_INTERVAL_S
            and self.queue.empty()
        ):
            client.partial_busy = True
            client.last_partial_at = now
            task = asyncio.create_task(self._partial(client, client.vad.snapshot(), client.utt_id), name="partial")
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _partial(self, client: Client, pcm: bytes, utt_id: str) -> None:
        """Live caption of the turn in progress. Dropped if the turn has
        already ended, so a slow partial never overwrites the final text."""

        try:
            hint = [code for code in {self.guide_lang, self.guest_lang} if code] if self.guest_lang else None
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.hub.stt_pool, self.engines.stt.transcribe, pcm, hint, client.partial_lang
            )
            if not result.text or utt_id != client.utt_id or not client.vad.in_speech:
                return
            # Lock the turn's language after the first second, so later partials
            # skip detection and the caption does not flicker between languages.
            if client.partial_lang is None and result.duration_ms >= 1000:
                client.partial_lang = result.lang
            lang = languages.normalize(result.lang) or self.guide_lang
            await self.hub.broadcast(
                "partial",
                utt=utt_id,
                text=result.text,
                lang=lang,
                speaker=self.resolve_speaker(lang, None, client.role),
            )
        except Exception as exc:
            self.logger.write("partial_failed", error=str(exc))
        finally:
            client.partial_busy = False

    async def handle_message(self, client: Client, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "set_stop":
            self.engagement.set_stop(str(message.get("stop") or ""))
            await self.hub.broadcast("tour_state", **self.state())
            await self._send_engagement()
        elif kind == "set_speaker":
            mode = str(message.get("mode") or "auto")
            self.speaker_mode = mode if mode in {"auto", "guide", "guest"} else "auto"
            await self.hub.broadcast("tour_state", roles={"guide"}, **self.state())
        elif kind == "pin_language":
            lang = languages.normalize(message.get("lang"))
            self.guest_lang_pinned = lang is not None
            if lang:
                self.guest_lang = lang
                self._note_guest_language(lang)
            await self.hub.broadcast("tour_state", **self.state())
        elif kind == "guest_language":
            # A guest screen chose its language: translate guide lines into it
            # too, without pinning the tour's detection for everyone else.
            lang = languages.normalize(message.get("lang"))
            if lang and lang != self.guide_lang:
                if lang in self.guest_languages:
                    self.guest_languages.remove(lang)
                self.guest_languages.append(lang)
                if self.guest_lang is None:
                    self.guest_lang = lang
                await self.hub.broadcast("tour_state", **self.state())
        elif kind == "guest_text":
            text = str(message.get("text") or "").strip()[:500]
            source = message.get("source") if message.get("source") in {"blink", "tap"} else "typed"
            lang = languages.normalize(message.get("lang")) or ("en" if source == "blink" else self.guest_lang or "en")
            if text:
                await self._enqueue(("text", {"text": text, "lang": lang, "speaker": "guest", "source": source}))
        elif kind in {"say_to_guest", "guide_text"}:
            text = str(message.get("text") or "").strip()[:600]
            if text:
                source = "copilot" if kind == "say_to_guest" else "typed"
                await self._enqueue(("text", {"text": text, "lang": self.guide_lang, "speaker": "guide", "source": source}))
        elif kind == "demo":
            if self._demo_task is None or self._demo_task.done():
                self.demo = True
                speed = float(message.get("speed") or 1.0)
                self._demo_task = asyncio.create_task(self._run_demo(max(0.2, min(speed, 5.0))), name="demo")
                self.tasks.add(self._demo_task)

    async def _enqueue(self, job: tuple[str, Any]) -> None:
        try:
            self.queue.put_nowait(job)
        except asyncio.QueueFull:
            await self._status("warning", "Too many turns waiting; one was dropped.")

    # ---------- Worker ----------

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            if job is None:
                return
            try:
                kind, data = job
                if kind == "audio":
                    await self._transcribe_and_process(*data)
                elif kind == "text":
                    await self.process_turn(**data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.write("worker_error", error=str(exc))
                await self._status("warning", f"Could not process a turn: {exc}")

    async def _transcribe_and_process(
        self, pcm: bytes, role: str, utt_id: str | None = None, ended_at: float | None = None, lang_hint: str | None = None
    ) -> None:
        hint = [code for code in {self.guide_lang, self.guest_lang} if code] if self.guest_lang else None
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        try:
            # The final pass re-detects the language over the whole turn; the
            # partials' guess only came from its first second.
            result = await loop.run_in_executor(self.hub.stt_pool, self.engines.stt.transcribe, pcm, hint)
        except Exception as exc:
            await self._status("error", f"Speech recognition is unavailable: {exc}")
            return
        finally:
            await self.hub.broadcast("listening", active=False, role=role, utt=utt_id)
        self.stt_latencies.append(int((time.monotonic() - started) * 1000))
        if not result.text:
            if utt_id:
                await self.hub.broadcast("partial_cancel", utt=utt_id)
            return
        if utt_id:
            # Final words on screen now; the translated turn replaces them a
            # moment later.
            lang = languages.normalize(result.lang) or self.guide_lang
            await self.hub.broadcast(
                "partial", utt=utt_id, text=result.text, lang=lang, final=True,
                speaker=self.resolve_speaker(lang, None, role),
            )
        await self.process_turn(
            text=result.text,
            lang=result.lang or self.guide_lang,
            speaker=None,
            source="voice",
            role=role,
            lang_prob=result.lang_prob,
            utt_id=utt_id,
            ended_at=ended_at,
        )

    def resolve_speaker(self, lang: str | None, speaker: str | None, role: str = "guide") -> str:
        if speaker in {"guide", "guest"}:
            return speaker
        if self.speaker_mode in {"guide", "guest"}:
            return self.speaker_mode
        if lang and lang != self.guide_lang:
            return "guest"
        if role == "guest":
            # The guest screen's own microphone is held by a visitor.
            return "guest"
        return "guide"

    async def process_turn(
        self,
        text: str,
        lang: str | None,
        speaker: str | None = None,
        source: str = "voice",
        role: str = "guide",
        lang_prob: float = 1.0,
        utt_id: str | None = None,
        ended_at: float | None = None,
    ) -> dict[str, Any]:
        lang = languages.normalize(lang) or self.guide_lang
        who = self.resolve_speaker(lang, speaker, role)
        if who == "guest" and lang != self.guide_lang and source != "blink" and not self.guest_lang_pinned:
            if lang != self.guest_lang:
                self.guest_lang = lang
                await self.hub.broadcast("tour_state", **self.state())
            self._note_guest_language(lang)

        text_en, ok_en = await self._translate(text, lang, "en")
        translations: dict[str, str] = {}
        if who == "guest":
            text_guide, ok_guide = await self._translate(text, lang, self.guide_lang)
            text_guest, ok_guest = text, True
        else:
            text_guide, ok_guide = text, True
            # A mixed group gets every line in each visitor's language (the
            # three most recent, to keep CPU translation inside a breath).
            targets = [code for code in self.guest_languages[-3:] if code != lang]
            translations, ok_guest = await self._translate_many(text, lang, targets)
            text_guest = translations.get(self.guest_lang or "", text)

        self._turn_seq += 1
        entry = {
            "id": f"t{self._turn_seq}",
            "at_ms": utc_ms(),
            "speaker": who,
            "source": source,
            "lang": lang,
            "lang_prob": round(lang_prob, 2),
            "text": text,
            "text_en": text_en,
            "text_guide": text_guide,
            "text_guest": text_guest,
            "translations": translations,
            "guest_lang": self.guest_lang,
            "stop": self.engagement.current_stop,
            "translated": ok_en and ok_guide and ok_guest,
            "utt": utt_id,
            # End of speech to text on screen, translation included.
            "latency_ms": int((time.monotonic() - ended_at) * 1000) if ended_at else None,
        }
        if who == "guest":
            entry["tags"] = self.engagement.ingest_guest(entry["id"], text_en, source)
        else:
            entry["deferred"] = self.engagement.ingest_guide(text_en)
            entry["used_suggestion"] = self._match_suggestion(text_en)
        self.turns.append(entry)
        self.logger.write("turn", **entry)
        await self.hub.broadcast("transcript", entry=entry)
        await self._send_engagement()

        if who == "guide" and translations:
            await self.broadcast_guest_line(text, lang, translations, entry["id"])
        if who == "guest":
            self._schedule_coach()
        return entry

    async def broadcast_guest_line(self, original: str, lang: str, translations: dict[str, str], turn_id: str) -> None:
        """Guest screens pick their own language out of `lines`; `lang` is the
        default for a screen that has not chosen."""

        lines = {
            code: {"text": text, "bcp47": languages.bcp47(code), "rtl": languages.is_rtl(code)}
            for code, text in translations.items()
        }
        await self.hub.broadcast(
            "guest_line",
            lines=lines,
            lang=self.guest_lang,
            original=original,
            original_lang=lang,
            speak=True,
            turn_id=turn_id,
        )

    async def _translate_many(self, text: str, src: str, targets: list[str]) -> tuple[dict[str, str], bool]:
        if not targets:
            return {}, True
        many = getattr(self.engines.translator, "translate_many", None)
        if many is None:
            out, ok_all = {}, True
            for code in targets:
                out[code], ok = await self._translate(text, src, code)
                ok_all = ok_all and ok
            return out, ok_all
        loop = asyncio.get_running_loop()
        try:
            out = await loop.run_in_executor(self.hub.mt_pool, many, text, src, targets)
            return out, self.engines.translator.ready
        except Exception as exc:
            self.logger.write("translate_failed", error=str(exc))
            return {code: text for code in targets}, False

    async def _translate(self, text: str, src: str | None, tgt: str | None) -> tuple[str, bool]:
        if not text or not src or not tgt or languages.normalize(src) == languages.normalize(tgt):
            return text, True
        loop = asyncio.get_running_loop()
        try:
            out = await loop.run_in_executor(self.hub.mt_pool, self.engines.translator.translate, text, src, tgt)
            return (out or text), bool(out) and self.engines.translator.ready
        except Exception as exc:
            self.logger.write("translate_failed", error=str(exc))
            return text, False

    def _note_guest_language(self, lang: str) -> None:
        if lang and lang != self.guide_lang and lang not in self.guest_languages:
            self.guest_languages.append(lang)

    def _match_suggestion(self, text_en: str) -> str | None:
        """Did Noor just say (roughly) the last suggestion? Feeds the
        'suggestions used' metric in the report."""

        for suggestion in self.suggestions[-3:]:
            if not suggestion.get("used") and _similarity(text_en, suggestion["say_en"]) >= 0.5:
                suggestion["used"] = True
                return suggestion["id"]
        return None

    # ---------- Coach ----------

    def _schedule_coach(self) -> None:
        if self._coach_task is not None and not self._coach_task.done():
            self._coach_task.cancel()
        self._coach_task = asyncio.create_task(self._run_coach(), name="coach")
        self.tasks.add(self._coach_task)
        self._coach_task.add_done_callback(self.tasks.discard)

    async def _run_coach(self) -> None:
        await asyncio.sleep(COACH_DEBOUNCE_S)
        turns = [
            Turn(t["speaker"], t["text_en"], t["source"], t.get("tags") or {})
            for t in self.turns[-12:]
        ]
        if not turns or turns[-1].speaker != "guest":
            return
        stop_id = self.engagement.current_stop
        snapshot = self.engagement.to_json()
        await self.hub.broadcast("thinking", roles={"guide"}, active=True)
        started = time.monotonic()
        result: CoachResult | None
        try:
            result = await asyncio.wait_for(
                self.coach.suggest(
                    turns,
                    stop_id,
                    self.engagement.stop_name(stop_id),
                    snapshot.get("hot_topic_label"),
                    use_llm=self.engines.llm_available,
                ),
                timeout=COACH_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            result = self.coach.rules(turns[-1], stop_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.write("coach_error", error=str(exc))
            result = self.coach.rules(turns[-1], stop_id)
        finally:
            await self.hub.broadcast("thinking", roles={"guide"}, active=False)
        self.coach_latencies.append(int((time.monotonic() - started) * 1000))
        if result is None or result.no_update:
            return
        await self.publish_suggestion(result, self.turns[-1]["id"])

    async def publish_suggestion(self, result: CoachResult, for_turn: str) -> dict[str, Any] | None:
        decision = self.filter.filter(result.say)
        if not decision.accepted:
            self.logger.write("filtered_suggestion", reason=decision.reason, text=result.say[:200])
            return None
        self.engagement.retag_topic(for_turn, result.topic)
        say_guide, _ = await self._translate(decision.text, "en", self.guide_lang)
        # Answer in the language of the visitor who spoke, not just the latest.
        asked = next((t for t in reversed(self.turns) if t["id"] == for_turn), None)
        guest = self.guest_lang
        if asked and asked["speaker"] == "guest" and asked["source"] != "blink" and asked["lang"] != self.guide_lang:
            guest = asked["lang"]
        say_guest, ok = (await self._translate(decision.text, "en", guest)) if guest else (decision.text, True)
        options = []
        for option in result.options or [{"label": "Answer", "say": decision.text}]:
            say, _ = await self._translate(option["say"], "en", self.guide_lang)
            options.append({"label": option["label"], "say": say, "say_en": option["say"]})
        suggestion = {
            "id": f"s{len(self.suggestions) + 1}",
            "options": options,
            "for_turn": for_turn,
            "at_ms": utc_ms(),
            "say_en": decision.text,
            "say": say_guide,
            "say_guest": say_guest,
            "guest_lang": guest,
            "why": result.why,
            "topic": result.topic,
            "next_step": result.next_step,
            "source": result.source,
            "stop": self.engagement.current_stop,
            "used": False,
            "latency_ms": self.coach_latencies[-1] if self.coach_latencies else None,
        }
        self.suggestions.append(suggestion)
        self.logger.write("suggestion", **suggestion)
        await self.hub.broadcast("suggestion", roles={"guide"}, **suggestion)
        await self._send_engagement()
        return suggestion

    # ---------- Demo ----------

    async def _run_demo(self, speed: float) -> None:
        try:
            script = json.loads(DEMO_SCRIPT.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            await self._status("error", f"Demo script missing: {exc}")
            return
        await self._status("info", "Playing the demo tour.")
        for step in script.get("steps", []):
            await asyncio.sleep(float(step.get("delay_s", 2.0)) / speed)
            if not self.running:
                return
            if step.get("stop"):
                self.engagement.set_stop(step["stop"])
                await self.hub.broadcast("tour_state", **self.state())
                await self._send_engagement()
            if step.get("text"):
                await self._enqueue(
                    (
                        "text",
                        {
                            "text": step["text"],
                            "lang": step.get("lang", "en"),
                            "speaker": step.get("speaker", "guest"),
                            "source": step.get("source", "voice"),
                        },
                    )
                )
                if step.get("speaker", "guest") == "guest":
                    # Give the coach its turn before the guide "replies".
                    await asyncio.sleep(1.0 / speed)
        await self._status("info", "Demo finished. Press End tour to see the report.")

    # ---------- Outputs ----------

    async def _send_engagement(self) -> None:
        payload = self.engagement.to_json()
        used = sum(1 for s in self.suggestions if s.get("used"))
        payload["suggestion_count"] = len(self.suggestions)
        payload["suggestions_used"] = used
        await self.hub.broadcast("engagement", roles={"guide"}, **payload)

    async def _status(self, level: str, message: str) -> None:
        await self.hub.broadcast("status", level=level, message=message)


def _avg(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


def _similarity(left: str, right: str) -> float:
    left_words = _normalize(left).split()
    right_words = _normalize(right).split()
    if not left_words or not right_words:
        return 0.0
    left_set = set(left_words)
    right_set = set(right_words)
    jaccard = len(left_set & right_set) / len(left_set | right_set)
    len_ratio = min(len(left_words), len(right_words)) / max(len(left_words), len(right_words))
    return (jaccard * 0.75) + (len_ratio * 0.25)
