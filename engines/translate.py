"""On-device translation with NLLB-200 (distilled 600M) on CTranslate2.

NLLB is the model the brief points at (FLORES-200 / NLLB-200): one 600M model
covers every language pair Noor's visitors bring, instead of one model per pair.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

import languages


class Translator(Protocol):
    ready: bool

    def translate(self, text: str, src: str | None, tgt: str | None) -> str: ...


class NllbTranslator:
    def __init__(self, model: str, device: str = "cpu", cache_size: int = 512):
        self.model_ref = model
        self.device = device
        self._translator = None
        self._sp = None
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._cache_size = cache_size
        self.error: str | None = None
        self._failed_at = 0.0

    @property
    def ready(self) -> bool:
        return self._translator is not None

    def load(self) -> None:
        with self._lock:
            if self._translator is not None:
                return
            if self.error and time.monotonic() - self._failed_at < 60:
                # Do not retry a missing model on every utterance.
                raise RuntimeError(self.error)
            try:
                import ctranslate2
                import sentencepiece as spm

                path = Path(self.model_ref)
                if not path.exists():
                    from huggingface_hub import snapshot_download

                    path = Path(snapshot_download(self.model_ref))
                sp_model = next(
                    (p for p in (path / "sentencepiece.bpe.model", path / "spm.model") if p.exists()),
                    None,
                )
                if sp_model is None:
                    found = list(path.glob("*.model"))
                    if not found:
                        raise FileNotFoundError(f"No SentencePiece model in {path}")
                    sp_model = found[0]
                self._sp = spm.SentencePieceProcessor(model_file=str(sp_model))
                self._translator = ctranslate2.Translator(
                    str(path), device=self.device, compute_type="int8"
                )
                self.error = None
            except Exception as exc:
                self.error = str(exc)
                self._failed_at = time.monotonic()
                raise

    def translate(self, text: str, src: str | None, tgt: str | None) -> str:
        text = (text or "").strip()
        src_code = languages.normalize(src)
        tgt_code = languages.normalize(tgt)
        if not text or not src_code or not tgt_code or src_code == tgt_code:
            return text
        key = (src_code, tgt_code, text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        self.load()
        src_flores = languages.flores(src_code)
        tgt_flores = languages.flores(tgt_code)
        sentences = _split_sentences(text)
        with self._lock:
            batch = [[src_flores] + self._sp.encode(s, out_type=str) + ["</s>"] for s in sentences]
            results = self._translator.translate_batch(
                batch,
                target_prefix=[[tgt_flores]] * len(batch),
                beam_size=1,
                max_decoding_length=256,
            )
            out = []
            for result in results:
                tokens = result.hypotheses[0]
                if tokens and tokens[0] == tgt_flores:
                    tokens = tokens[1:]
                out.append(self._sp.decode(tokens))
        translated = " ".join(part.strip() for part in out if part.strip())
        self._cache[key] = translated
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return translated


    def translate_many(self, text: str, src: str | None, targets: list[str]) -> dict[str, str]:
        """One line into several languages in a single NLLB batch: a guide line
        for a mixed group costs one decode, not one per language."""

        text = (text or "").strip()
        src_code = languages.normalize(src)
        wanted = [t for t in dict.fromkeys(languages.normalize(t) for t in targets) if t]
        out = {t: text for t in wanted if t == src_code or not text}
        todo = [t for t in wanted if t not in out and (src_code, t, text) not in self._cache]
        for t in wanted:
            if t not in out and (src_code, t, text) in self._cache:
                out[t] = self._cache[(src_code, t, text)]
        if todo and src_code:
            self.load()
            sentences = _split_sentences(text)
            with self._lock:
                source = [[languages.flores(src_code)] + self._sp.encode(s, out_type=str) + ["</s>"] for s in sentences]
                batch, prefixes = [], []
                for t in todo:
                    batch.extend(source)
                    prefixes.extend([[languages.flores(t)]] * len(source))
                results = self._translator.translate_batch(batch, target_prefix=prefixes, beam_size=1, max_decoding_length=256)
            for i, t in enumerate(todo):
                parts = []
                for result in results[i * len(source) : (i + 1) * len(source)]:
                    tokens = result.hypotheses[0]
                    if tokens and tokens[0] == languages.flores(t):
                        tokens = tokens[1:]
                    parts.append(self._sp.decode(tokens).strip())
                out[t] = " ".join(p for p in parts if p)
                self._cache[(src_code, t, text)] = out[t]
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return out


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?؟。！？])\s+", text)
    return [p for p in parts if p.strip()] or [text]


class PassthroughTranslator:
    """Used when NLLB is not installed: the line is shown as spoken, flagged
    as untranslated, rather than the tour stopping."""

    ready = False
    error = "NLLB not loaded"

    def translate(self, text: str, src: str | None, tgt: str | None) -> str:
        return text


class FakeTranslator:
    """Deterministic stand-in for tests: tags the target language."""

    ready = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    def translate(self, text: str, src: str | None, tgt: str | None) -> str:
        self.calls.append((text, src, tgt))
        if not text or languages.normalize(src) == languages.normalize(tgt):
            return text
        return f"[{languages.normalize(tgt)}] {text}"
