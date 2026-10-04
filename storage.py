"""Local JSON store. Replaces orbit.ai's Firestore layer.

A small operator runs this on one laptop or phone, often offline, so the data
lives in plain files she can back up by copying a folder.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR, get_settings

_LOCK = threading.Lock()
SAMPLE_PROFILE = DATA_DIR / "sample_profile.json"
SAMPLE_REVIEWS = DATA_DIR / "sample_reviews.csv"


def _root() -> Path:
    root = get_settings().data_dir
    (root / "tours").mkdir(parents=True, exist_ok=True)
    return root


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


# ---------- Operator profile ----------

def get_profile() -> dict[str, Any]:
    saved = _read(_root() / "profile.json", None)
    if saved:
        return saved
    return _read(SAMPLE_PROFILE, {})


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    _write(_root() / "profile.json", profile)
    return profile


# ---------- Tours ----------

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def new_tour_id() -> str:
    return time.strftime("tour_%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"


def save_tour(tour: dict[str, Any]) -> None:
    tour_id = str(tour.get("id", ""))
    if not _SAFE_ID.match(tour_id):
        raise ValueError("Invalid tour id.")
    _write(_root() / "tours" / f"{tour_id}.json", tour)


def get_tour(tour_id: str) -> dict[str, Any] | None:
    if not _SAFE_ID.match(tour_id or ""):
        return None
    return _read(_root() / "tours" / f"{tour_id}.json", None)


def list_tours() -> list[dict[str, Any]]:
    tours = []
    for path in sorted((_root() / "tours").glob("*.json"), reverse=True):
        tour = _read(path, None)
        if not tour:
            continue
        engagement = tour.get("engagement") or {}
        tours.append(
            {
                "id": tour.get("id"),
                "started_at": tour.get("started_at"),
                "ended_at": tour.get("ended_at"),
                "tour_type": tour.get("tour_type"),
                "guest_languages": tour.get("guest_languages", []),
                "turn_count": len(tour.get("turns", [])),
                "question_count": engagement.get("question_count", 0),
                "hot_stop": engagement.get("hot_stop"),
                "demo": bool(tour.get("demo")),
            }
        )
    return tours


def all_tours_full() -> list[dict[str, Any]]:
    return [t for t in (_read(p, None) for p in (_root() / "tours").glob("*.json")) if t]


# ---------- Reviews ----------

def list_reviews() -> list[dict[str, Any]]:
    return _read(_root() / "reviews.json", [])


def add_reviews(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviews = list_reviews()
    seen = {(r.get("text") or "").strip() for r in reviews}
    next_id = len(reviews) + 1
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rating = item.get("rating")
        try:
            rating = float(rating) if rating not in (None, "") else None
        except (TypeError, ValueError):
            rating = None
        reviews.append(
            {
                "id": f"R{next_id}",
                "text": text[:4000],
                "rating": rating,
                "source": str(item.get("source") or "pasted")[:40],
                "date": str(item.get("date") or "")[:20],
                "lang": item.get("lang"),
                "text_en": item.get("text_en"),
            }
        )
        next_id += 1
    _write(_root() / "reviews.json", reviews)
    return reviews


def update_reviews(reviews: list[dict[str, Any]]) -> None:
    _write(_root() / "reviews.json", reviews)


def clear_reviews() -> None:
    _write(_root() / "reviews.json", [])


def parse_reviews(raw: str) -> list[dict[str, Any]]:
    """Accept a CSV with a text/review column, or one review per blank-line block."""

    raw = (raw or "").strip()
    if not raw:
        return []
    first_line = raw.splitlines()[0].lower()
    if "," in first_line and any(key in first_line for key in ("text", "review", "comment")):
        reader = csv.DictReader(io.StringIO(raw))
        items = []
        for row in reader:
            lowered = {str(k).strip().lower(): v for k, v in row.items() if k}
            text = lowered.get("text") or lowered.get("review") or lowered.get("comment")
            if text:
                items.append(
                    {
                        "text": text,
                        "rating": lowered.get("rating") or lowered.get("stars"),
                        "source": lowered.get("source") or "csv",
                        "date": lowered.get("date") or "",
                    }
                )
        return items
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    if len(blocks) == 1:
        blocks = [line.strip() for line in raw.splitlines() if line.strip()]
    return [{"text": block, "source": "pasted"} for block in blocks]


def sample_reviews() -> list[dict[str, Any]]:
    try:
        return parse_reviews(SAMPLE_REVIEWS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


# ---------- Insights cache ----------

def save_insights(data: dict[str, Any]) -> None:
    _write(_root() / "insights.json", data)


def get_insights() -> dict[str, Any] | None:
    return _read(_root() / "insights.json", None)
