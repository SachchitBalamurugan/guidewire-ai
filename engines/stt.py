"""On-device speech to text with faster-whisper (CTranslate2, int8 on CPU)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

import languages


@dataclass
class SttResult:
    text: str
    lang: str | None
    lang_prob: float
    duration_ms: int


class SpeechToText(Protocol):
    ready: bool

    def transcribe(self, pcm: bytes, hint: list[str] | None = None, language: str | None = None) -> SttResult: ...


class WhisperSTT:
    """Lazy-loaded so the server starts instantly; the first utterance (or the
    startup warm-up) pays the model load."""

    def __init__(self, model: str = "small", device: str = "cpu", compute_type: str = "int8"):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()
        self.error: str | None = None
        self._failed_at = 0.0

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if self.error and time.monotonic() - self._failed_at < 60:
                # Do not retry a missing model on every utterance.
                raise RuntimeError(self.error)
            try:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type
                )
                self.error = None
            except Exception as exc:  # surfaced on /api/health
                self.error = str(exc)
                self._failed_at = time.monotonic()
                raise

    def transcribe(self, pcm: bytes, hint: list[str] | None = None, language: str | None = None) -> SttResult:
        """One encoder pass over just the speech, then language ID and decoding
        from that same pass.

        faster-whisper's transcribe() pads every clip to Whisper's 30 s window
        and, when the language is unknown, encodes it a second time to detect
        it: about 2.4 s for a 4 s question on this CPU. Encoding only the real
        length (plus a little silence) gives the same text in about 0.5 s.
        """

        import numpy as np

        self.load()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        duration_ms = int(len(audio) / 16)
        if len(audio) < 1600:
            return SttResult("", None, 0.0, duration_ms)
        with self._lock:
            try:
                text, lang, prob = self._fast(audio, hint, language)
            except Exception:
                text, lang, prob = self._slow(audio, language)
        return SttResult(
            text=_clean(text),
            lang=languages.normalize(lang) or lang,
            lang_prob=float(prob),
            duration_ms=duration_ms,
        )

    def _fast(self, audio, hint: list[str] | None, language: str | None) -> tuple[str, str | None, float]:
        import ctranslate2
        import numpy as np
        from faster_whisper.tokenizer import Tokenizer

        model = self._model
        features = model.feature_extractor(audio)
        # 100 frames = 1 s. Pad with a second of silence and never go below
        # 6 s: very short windows make Whisper invent words.
        frames = min(3000, max(600, features.shape[1] + 100))
        if features.shape[1] < frames:
            features = np.pad(features, ((0, 0), (0, frames - features.shape[1])))
        else:
            features = features[:, :frames]
        encoded = model.model.encode(
            ctranslate2.StorageView.from_array(np.ascontiguousarray(features[None], dtype=np.float32)),
            to_cpu=False,
        )
        prob = 1.0
        if language is None:
            ranked = [(token[2:-2], p) for token, p in model.model.detect_language(encoded)[0]]
            language, prob = _choose_language(ranked, hint)
        tokenizer = Tokenizer(model.hf_tokenizer, True, task="transcribe", language=language)
        prompt = list(tokenizer.sot_sequence) + [tokenizer.no_timestamps]
        seconds = len(audio) / 16000
        result = model.model.generate(
            encoded,
            [prompt],
            beam_size=1,
            # ~4 tokens a second of speech is generous; the cap stops a
            # hallucination loop from eating the CPU.
            max_length=int(20 + seconds * 8),
            suppress_blank=True,
            suppress_tokens=[-1],
            repetition_penalty=1.1,
        )
        text = tokenizer.decode(result[0].sequences_ids[0]).strip()
        return text, language, prob

    def _slow(self, audio, language: str | None) -> tuple[str, str | None, float]:
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=1,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, language or info.language, float(info.language_probability or 1.0)


def _choose_language(ranked: list[tuple[str, float]], hint: list[str] | None) -> tuple[str, float]:
    """Prefer the languages this tour expects. On a two-second "how much?"
    Whisper's open-set guess wanders; choosing between the guide's and the
    guest's language does not. A confident new language is a new visitor."""

    top_code, top_prob = ranked[0]
    if not hint or top_code in hint or top_prob >= 0.8:
        return top_code, top_prob
    allowed = [(code, p) for code, p in ranked if code in hint]
    return max(allowed, key=lambda item: item[1]) if allowed else (top_code, top_prob)


# Whisper's well-known hallucinations on near-silence.
_HALLUCINATIONS = {
    "thank you.",
    "thanks for watching!",
    "thank you for watching.",
    "you",
    "bye.",
    "subtitles by the amara.org community",
}


def _clean(text: str) -> str:
    if text.strip().lower() in _HALLUCINATIONS:
        return ""
    return text.strip()


class FakeSTT:
    """Returns queued results in order. Used by tests and the text demo."""

    ready = True

    def __init__(self, results: list[SttResult] | None = None):
        self.results = list(results or [])

    def transcribe(self, pcm: bytes, hint: list[str] | None = None, language: str | None = None) -> SttResult:
        if language is not None:
            return SttResult("", language, 1.0, len(pcm) // 32)  # partials stay quiet in tests
        if self.results:
            return self.results.pop(0)
        return SttResult("", None, 0.0, len(pcm) // 32)
