from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at runtime
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = ROOT / "prompts"
PLAYBOOKS_DIR = PROMPTS_DIR / "playbooks"
DATA_DIR = ROOT / "data"

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")

DEFAULT_TOUR_TYPE = "farm_tour"
TOUR_TYPES = {
    "farm_tour": "Farm walk",
    "food_tasting": "Food tasting",
    "workshop": "Hands-on workshop",
}


@dataclass(frozen=True)
class Settings:
    # Speech to text (faster-whisper). "small" is the smallest Whisper that
    # still identifies the language reliably on short tour questions.
    stt_model: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    # Translation (NLLB-200 distilled, converted to CTranslate2).
    nllb_model: str = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
    # Suggestions and insights. "ollama" is the on-device default; "rules"
    # needs no model at all; "gemini" is the optional cloud fallback.
    llm_backend: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    llm_timeout_s: float = 25.0
    preload_models: bool = True
    debug: bool = False
    data_dir: Path = DATA_DIR / "store"
    log_dir: Path = ROOT / "logs"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    defaults = Settings()
    return Settings(
        stt_model=os.getenv("STT_MODEL", defaults.stt_model),
        stt_device=os.getenv("STT_DEVICE", defaults.stt_device),
        stt_compute_type=os.getenv("STT_COMPUTE_TYPE", defaults.stt_compute_type),
        nllb_model=os.getenv("NLLB_MODEL", defaults.nllb_model),
        llm_backend=os.getenv("LLM_BACKEND", defaults.llm_backend).strip().lower(),
        ollama_url=os.getenv("OLLAMA_URL", defaults.ollama_url).rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", defaults.ollama_model),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", defaults.gemini_model),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", defaults.llm_timeout_s)),
        preload_models=_bool("PRELOAD_MODELS", defaults.preload_models),
        debug=_bool("DEBUG", defaults.debug),
        data_dir=Path(os.getenv("DATA_DIR", str(defaults.data_dir))),
        log_dir=Path(os.getenv("LOG_DIR", str(defaults.log_dir))),
    )


def normalize_tour_type(value: str | None) -> str:
    slug = (value or "").strip().lower()
    return slug if slug in TOUR_TYPES else DEFAULT_TOUR_TYPE


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def load_playbook(tour_type: str | None) -> str:
    path = PLAYBOOKS_DIR / f"{normalize_tour_type(tour_type)}.txt"
    return path.read_text(encoding="utf-8").strip()


def render(template: str, values: dict[str, str]) -> str:
    """Fill {{placeholders}}. Unknown ones become empty so none reach a model."""

    import re

    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: str(values.get(m.group(1), "")),
        template,
    )
