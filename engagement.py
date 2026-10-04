"""Where are visitors most engaged? Adapted from orbit.ai's CallAnalytics.

orbit.ai scored one buyer's mood toward a close. A tour has many visitors and
moves through stops, so the unit here is the question: every visitor question
is credited to the stop the group was at and to a topic. Stops and topics with
the most questions are where visitors' curiosity, and so the business value,
sits. Keyword rules run on the English pivot text, so they work whatever
language the visitor spoke.
"""

from __future__ import annotations

import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

POSITIVE_TERMS = {
    "amazing", "beautiful", "best", "delicious", "enjoy", "enjoyed", "excellent",
    "fantastic", "fascinating", "favourite", "favorite", "good", "great", "incredible",
    "interesting", "love", "lovely", "perfect", "tasty", "thank", "wonderful", "wow",
    "yummy", "special", "authentic",
}

NEGATIVE_TERMS = {
    "bad", "bored", "boring", "cold", "confused", "difficult", "dirty", "expensive",
    "hard", "hot", "hungry", "long", "tired", "too much", "uncomfortable", "worried",
    "disappointed", "slow", "far", "crowded",
}

# Closest open template is MASSIVE's intent labels; this is the fixed list a
# small operator actually needs to act on.
INTENTS: dict[str, tuple[str, ...]] = {
    "price": ("how much", "price", "cost", "expensive", "cheap", "pay", "money"),
    "buy": ("buy", "purchase", "take home", "sell", "bottle", "jar", "shop", "bring back"),
    "booking": ("book", "reserve", "come back", "again", "return", "next time", "friends", "group", "harvest"),
    "directions": ("where", "toilet", "bathroom", "parking", "bus", "taxi", "far", "way to"),
    "food": ("eat", "food", "lunch", "taste", "recipe", "vegetarian", "vegan", "gluten", "allergy", "cook"),
    "accessibility": ("wheelchair", "accessible", "step", "rest", "sit", "slow", "help"),
    "history": ("history", "old", "year", "family", "generation", "since", "tradition"),
    "how_made": ("how do you", "how is", "how are", "how does", "make", "made", "process", "press", "grow"),
}

NEXT_STEP_TERMS = {
    "buy": ("buy", "take home", "purchase", "i'll take", "can i get"),
    "book": ("book", "reserve", "workshop", "harvest"),
    "return": ("come back", "return", "next year", "again"),
    "referral": ("tell my friends", "bring my", "recommend", "friends"),
    "review": ("review", "tripadvisor", "google maps", "five stars", "5 stars"),
}

QUESTION_STARTS = (
    "what", "how", "why", "where", "when", "who", "which", "can", "could", "do", "does",
    "did", "is", "are", "was", "will", "would", "should", "may", "have", "has",
)

DEFERRAL_PATTERNS = (
    "i'll check", "i will check", "let me check", "i'm not sure", "i am not sure",
    "i don't know", "i do not know", "i'll find out", "i will find out", "ask my",
)


def is_question(text: str) -> bool:
    clean = (text or "").strip().lower()
    if not clean:
        return False
    if clean.endswith("?") or "?" in clean[-3:] or "؟" in clean:
        return True
    first = re.split(r"[\s,]+", clean, maxsplit=1)[0]
    return first in QUESTION_STARTS


def classify_intent(text: str) -> str:
    clean = _normalize(text)
    best, best_hits = "other", 0
    for intent, terms in INTENTS.items():
        hits = sum(1 for term in terms if _has(clean, term))
        if hits > best_hits:
            best, best_hits = intent, hits
    return best


@dataclass
class Topic:
    id: str
    label: str
    keywords: tuple[str, ...]


@dataclass
class TourEngagement:
    stops: list[dict[str, str]] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    current_stop: str | None = None
    guest_turns: int = 0
    guide_turns: int = 0
    question_count: int = 0
    blink_turns: int = 0
    positive_hits: int = 0
    negative_hits: int = 0
    questions_by_stop: Counter = field(default_factory=Counter)
    turns_by_stop: Counter = field(default_factory=Counter)
    positive_by_stop: Counter = field(default_factory=Counter)
    questions_by_topic: Counter = field(default_factory=Counter)
    intents: Counter = field(default_factory=Counter)
    next_steps: Counter = field(default_factory=Counter)
    questions: list[dict[str, Any]] = field(default_factory=list)
    deferred: list[dict[str, Any]] = field(default_factory=list)
    mood_history: deque = field(default_factory=lambda: deque(maxlen=40))
    stop_log: list[dict[str, Any]] = field(default_factory=list)
    _last_guest_question: dict[str, Any] | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "TourEngagement":
        stops = [
            {"id": str(s.get("id")), "name": str(s.get("name", s.get("id")))}
            for s in profile.get("stops", [])
            if s.get("id")
        ]
        topics = [
            Topic(str(t["id"]), str(t.get("label", t["id"])), tuple(k.lower() for k in t.get("keywords", [])))
            for t in profile.get("topics", [])
            if t.get("id")
        ]
        engagement = cls(stops=stops, topics=topics)
        if stops:
            engagement.set_stop(stops[0]["id"])
        return engagement

    def set_stop(self, stop_id: str | None) -> None:
        if not stop_id or stop_id == self.current_stop:
            return
        if self.stops and stop_id not in {s["id"] for s in self.stops}:
            return
        self.current_stop = stop_id
        self.stop_log.append({"stop": stop_id, "at_ms": _ms()})

    def stop_name(self, stop_id: str | None) -> str:
        for stop in self.stops:
            if stop["id"] == stop_id:
                return stop["name"]
        return stop_id or "Unknown"

    def classify_topic(self, text: str) -> str | None:
        clean = _normalize(text)
        best, best_hits = None, 0
        for topic in self.topics:
            hits = sum(1 for kw in topic.keywords if _has(clean, kw))
            if hits > best_hits:
                best, best_hits = topic.id, hits
        return best

    def ingest_guest(self, turn_id: str, text_en: str, source: str = "voice") -> dict[str, Any]:
        """Credit one finished visitor turn. Returns the turn's tags."""

        stop = self.current_stop or "unknown"
        self.guest_turns += 1
        self.turns_by_stop[stop] += 1
        if source == "blink":
            self.blink_turns += 1
        clean = _normalize(text_en)
        positive = [t for t in POSITIVE_TERMS if _has(clean, t)]
        negative = [t for t in NEGATIVE_TERMS if _has(clean, t)]
        self.positive_hits += len(positive)
        self.negative_hits += len(negative)
        self.positive_by_stop[stop] += len(positive)
        mood = "positive" if len(positive) > len(negative) else "negative" if negative else "neutral"
        self.mood_history.append(mood)

        question = is_question(text_en)
        intent = classify_intent(text_en)
        topic = self.classify_topic(text_en)
        for step, terms in NEXT_STEP_TERMS.items():
            if any(_has(clean, term) for term in terms):
                self.next_steps[step] += 1

        tags = {
            "turn_id": turn_id,
            "stop": stop,
            "question": question,
            "intent": intent,
            "topic": topic,
            "mood": mood,
            "source": source,
        }
        if question:
            self.question_count += 1
            self.questions_by_stop[stop] += 1
            if topic:
                self.questions_by_topic[topic] += 1
            self.intents[intent] += 1
            record = {**tags, "text": text_en[:300], "at_ms": _ms(), "answered": None}
            self.questions.append(record)
            self._last_guest_question = record
        return tags

    def ingest_guide(self, text_en: str) -> bool:
        """Noor's reply. Returns True when it defers the last question, which
        the report lists as unanswered so she can fill the gap in her briefing."""

        self.guide_turns += 1
        clean = _normalize(text_en)
        last = self._last_guest_question
        if last is None or last.get("answered") is not None:
            return False
        deferred = any(p in clean for p in DEFERRAL_PATTERNS)
        last["answered"] = not deferred
        if deferred:
            self.deferred.append(last)
        return deferred

    def retag_topic(self, turn_id: str, topic: str | None) -> None:
        """The coach's model can label a question the keyword rules missed."""

        if not topic or topic not in {t.id for t in self.topics}:
            return
        for record in self.questions:
            if record["turn_id"] == turn_id and record.get("topic") != topic:
                if record.get("topic"):
                    self.questions_by_topic[record["topic"]] -= 1
                    if self.questions_by_topic[record["topic"]] <= 0:
                        del self.questions_by_topic[record["topic"]]
                record["topic"] = topic
                self.questions_by_topic[topic] += 1
                return

    def stop_scores(self) -> list[dict[str, Any]]:
        names = self.stops or [{"id": s, "name": s} for s in self.turns_by_stop]
        rows = []
        for stop in names:
            sid = stop["id"]
            q = self.questions_by_stop.get(sid, 0)
            rows.append(
                {
                    "id": sid,
                    "name": stop["name"],
                    "questions": q,
                    "turns": self.turns_by_stop.get(sid, 0),
                    "positive": self.positive_by_stop.get(sid, 0),
                    # Questions weigh most: a question is a visitor choosing to
                    # spend attention. Delight counts, but less.
                    "score": q * 2 + self.positive_by_stop.get(sid, 0),
                }
            )
        return rows

    def to_json(self) -> dict[str, Any]:
        stops = self.stop_scores()
        hot_stop = max(stops, key=lambda r: (r["score"], r["questions"]), default=None)
        topic_labels = {t.id: t.label for t in self.topics}
        topics = [
            {"id": tid, "label": topic_labels.get(tid, tid), "questions": n}
            for tid, n in self.questions_by_topic.most_common()
        ]
        mood_counts = Counter(self.mood_history)
        overall = (
            "positive" if self.positive_hits >= self.negative_hits + 2
            else "negative" if self.negative_hits >= self.positive_hits + 2
            else "neutral"
        )
        return {
            "current_stop": self.current_stop,
            "guest_turns": self.guest_turns,
            "guide_turns": self.guide_turns,
            "question_count": self.question_count,
            "blink_turns": self.blink_turns,
            "stops": stops,
            "hot_stop": hot_stop["id"] if hot_stop and hot_stop["score"] > 0 else None,
            "hot_stop_name": hot_stop["name"] if hot_stop and hot_stop["score"] > 0 else None,
            "topics": topics,
            "hot_topic": topics[0]["id"] if topics else None,
            "hot_topic_label": topics[0]["label"] if topics else None,
            "intents": dict(self.intents.most_common()),
            "next_steps": dict(self.next_steps),
            "overall_mood": overall,
            "mood_counts": dict(mood_counts),
            "positive_signal_count": self.positive_hits,
            "negative_signal_count": self.negative_hits,
            "deferred_questions": [q["text"] for q in self.deferred],
            "recent_questions": [
                {"text": q["text"], "stop": q["stop"], "topic": q.get("topic"), "intent": q["intent"], "source": q["source"]}
                for q in self.questions[-8:]
            ],
        }

    def snapshot(self) -> dict[str, Any]:
        """Everything the post-tour report needs, including the full question list."""

        data = self.to_json()
        data["questions"] = list(self.questions)
        data["stop_log"] = list(self.stop_log)
        return data


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("’", "'")).strip()


def _has(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None


def _ms() -> int:
    return int(time.time() * 1000)
