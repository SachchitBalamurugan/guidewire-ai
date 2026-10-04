"""What should Noor say next?

The small model gets orbit.ai's coaching discipline (answer direct questions
first, never invent, one question at a time) retargeted at a tour. When no
model is reachable, or it times out, a rule layer answers from the farm's own
FAQ and stop facts, so the screen never goes blank mid-tour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from config import TOUR_TYPES, load_playbook, load_prompt, normalize_tour_type, render
from engagement import NEGATIVE_TERMS, NEXT_STEP_TERMS, POSITIVE_TERMS, _has, _normalize
from engines.llm import LLM

NEXT_STEPS = {"none", "buy", "book", "return", "referral", "review"}
_STOPWORDS = {
    "the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "we", "it", "this",
    "that", "of", "to", "in", "on", "for", "and", "can", "how", "what", "there", "here",
    "be", "with", "my", "me", "have", "has",
}


@dataclass
class CoachResult:
    say: str
    why: str = ""
    topic: str | None = None
    next_step: str = "none"
    source: str = "rules"
    # The line above plus alternatives Noor can pick with one tap:
    # [{"label": "Answer", "say": ...}, {"label": "Go deeper", ...}, {"label": "Next step", ...}]
    options: list[dict[str, str]] = field(default_factory=list)

    @property
    def no_update(self) -> bool:
        return self.say.strip().upper().strip('"') == "NO_UPDATE"


@dataclass
class Turn:
    speaker: str  # "guide" | "guest"
    text_en: str
    source: str = "voice"
    tags: dict[str, Any] = field(default_factory=dict)


def format_briefing(profile: dict[str, Any]) -> str:
    lines = [
        f"Business: {profile.get('business_name', '')} ({profile.get('location', '')})",
        f"Description: {profile.get('description', '')}",
        f"Tour length: {profile.get('tour_length', '')}",
    ]
    for stop in profile.get("stops", []):
        facts = " ".join(stop.get("facts", []))
        lines.append(f"Stop '{stop.get('name')}' [{stop.get('id')}]: {facts}")
    if profile.get("products"):
        lines.append(
            "Products for sale: "
            + "; ".join(f"{p.get('name')} - {p.get('price')}" for p in profile["products"])
        )
    for offer in profile.get("offers", []):
        lines.append(f"Offer: {offer}")
    if profile.get("booking"):
        lines.append(f"Booking: {profile['booking']}")
    for item in profile.get("faq", []):
        lines.append(f"FAQ: {item.get('q')} -> {item.get('a')}")
    if profile.get("topics"):
        lines.append("Topic ids: " + ", ".join(t["id"] for t in profile["topics"] if t.get("id")))
    for claim in profile.get("claims_not_to_make", []):
        lines.append(f"Never: {claim}")
    return "\n".join(lines)


class Coach:
    def __init__(self, llm: LLM, profile: dict[str, Any], tour_type: str | None = None):
        self.llm = llm
        self.profile = profile
        self.tour_type = normalize_tour_type(tour_type)
        self._template = load_prompt("tour_guide_prompt.txt")
        self._playbook = load_playbook(self.tour_type)
        self._briefing = format_briefing(profile)
        self._fact_cursor: dict[str, int] = {}
        self._mentioned_products: set[str] = set()
        self._asked_highlight = False
        self.llm_ok: bool | None = None

    def system_prompt(self, current_stop: str) -> str:
        return render(
            self._template,
            {
                "operator_name": self.profile.get("operator_name", "the guide"),
                "business_name": self.profile.get("business_name", "the farm"),
                "location": self.profile.get("location", ""),
                "tour_type_label": TOUR_TYPES.get(self.tour_type, "tour"),
                "current_stop": current_stop,
                "tour_type_playbook": self._playbook,
                "briefing": self._briefing,
            },
        )

    async def suggest(
        self,
        turns: list[Turn],
        current_stop_id: str | None,
        current_stop_name: str,
        hot_topic_label: str | None = None,
        use_llm: bool = True,
    ) -> CoachResult | None:
        if not turns:
            return None
        last = turns[-1]
        if use_llm:
            user = self._user_prompt(turns, current_stop_name, hot_topic_label)
            reply = await self.llm.complete_json(self.system_prompt(current_stop_name), user, max_tokens=360)
            self.llm_ok = reply is not None
            result = _from_reply(reply)
            if result is not None:
                return self.with_options(result, last, current_stop_id)
        return self.rules(last, current_stop_id)

    def _user_prompt(self, turns: list[Turn], stop_name: str, hot_topic: str | None) -> str:
        lines = []
        for turn in turns[-12:]:
            who = "Noor" if turn.speaker == "guide" else "Visitor"
            mark = " [blink]" if turn.source == "blink" else ""
            lines.append(f"{who}{mark}: {turn.text_en}")
        hot = f"\nVisitors have asked the most about: {hot_topic}." if hot_topic else ""
        return (
            f"Current stop: {stop_name}.{hot}\n\nRecent conversation (translated to English):\n"
            + "\n".join(lines)
            + "\n\nWhat should Noor say next? Reply with the JSON only."
        )

    # ---------- Rule layer ----------

    def rules(self, last: Turn, stop_id: str | None) -> CoachResult | None:
        result = self._primary_rule(last, stop_id)
        return self.with_options(result, last, stop_id) if result is not None else None

    def with_options(self, result: CoachResult, last: Turn, stop_id: str | None) -> CoachResult:
        """Give Noor a choice: the suggested line, a way to go deeper, and a
        gentle next step. Model-written options are kept; gaps are filled from
        the briefing."""

        if result.no_update:
            return result
        label = "Answer" if (last.tags or {}).get("question") else "Respond"
        options = [{"label": label, "say": result.say}]
        seen = {_normalize(result.say)}
        for option in result.options:
            say = str(option.get("say") or "").strip()
            if say and _normalize(say) not in seen and len(options) < 3:
                options.append({"label": str(option.get("label") or "Option")[:24], "say": say[:400]})
                seen.add(_normalize(say))
        labels = {o["label"].lower() for o in options}
        if len(options) < 3 and "go deeper" not in labels:
            deeper = self._deeper(stop_id, (last.tags or {}).get("topic"))
            if deeper and _normalize(deeper) not in seen:
                options.append({"label": "Go deeper", "say": deeper})
                seen.add(_normalize(deeper))
        if len(options) < 3 and "next step" not in labels:
            step = self._next_step_line(stop_id, (last.tags or {}).get("topic"))
            if step and _normalize(step) not in seen and not any(w in result.say.lower() for w in ("jod", "whatsapp")):
                options.append({"label": "Next step", "say": step})
        result.options = options
        return result

    def _deeper(self, stop_id: str | None, topic: str | None) -> str | None:
        for stop in self.profile.get("stops", []):
            if stop.get("id") == stop_id:
                facts = stop.get("facts", [])
                index = self._fact_cursor.get(stop_id, 0)
                if index < len(facts):
                    return f"Here's something most people don't know: {facts[index]}"
        return "What made you curious about that?"

    def _next_step_line(self, stop_id: str | None, topic: str | None) -> str | None:
        product = self._product_for(stop_id, topic)
        if product:
            return f"If you'd like to take some home, the {product['name'].lower()} is {product['price']}."
        offer = next((o for o in self.profile.get("offers", []) if "harvest" in o.lower()), None)
        if offer:
            return f"If you enjoy this, you could come back for the harvest. {offer}"
        return "If you enjoyed today, a short review would mean a lot to us."

    def _primary_rule(self, last: Turn, stop_id: str | None) -> CoachResult | None:
        if last.speaker != "guest":
            return None
        text = last.text_en.strip()
        clean = _normalize(text)
        tags = last.tags or {}
        question = bool(tags.get("question"))
        topic = tags.get("topic")

        step = next((s for s, terms in NEXT_STEP_TERMS.items() if any(_has(clean, t) for t in terms)), None)

        if question:
            answer = self.answer(text, stop_id)
            if answer is not None:
                follow = self._follow_up(stop_id)
                say = f"{answer} {follow}".strip() if follow and len(answer.split()) < 22 else answer
                return CoachResult(say, "Answer from your FAQ", topic, _next_step_for(tags), "rules")
            return CoachResult(
                "That's a good question, and I want to get it right. I'll check and tell you before you leave.",
                "Not in your briefing: add it after the tour",
                topic,
                "none",
                "rules",
            )

        if step in {"return", "book", "referral"}:
            offer = next((o for o in self.profile.get("offers", []) if "harvest" in o.lower()), None)
            booking = self.profile.get("booking", "")
            say = "We'd love that." + (f" {offer}" if offer else "") + (f" {booking}" if booking else "")
            return CoachResult(say.strip(), "They want to come back: make it easy", "booking", "book" if step == "book" else step, "rules")

        if any(_has(clean, t) for t in NEGATIVE_TERMS):
            return CoachResult(
                "Let's take a short break in the shade. Would anyone like water or some sage tea?",
                "Visitor sounds tired or uncomfortable",
                "logistics",
                "none",
                "rules",
            )

        if any(_has(clean, t) for t in POSITIVE_TERMS):
            product = self._product_for(stop_id, topic)
            if product:
                self._mentioned_products.add(product["name"])
                return CoachResult(
                    f"I'm so glad you like it. If you'd like to take some home, the {product['name'].lower()} is {product['price']} in the courtyard.",
                    "Delight is the moment to mention the product once",
                    topic or "buying",
                    "buy",
                    "rules",
                )
            return CoachResult(
                "I'm glad you're enjoying it. What would you like to know more about here?",
                "Follow their curiosity",
                topic,
                "none",
                "rules",
            )

        fact = self._next_fact(stop_id)
        if fact:
            return CoachResult(
                f"{fact} What would you like to know about this?",
                "Share a fact, then invite a question",
                topic,
                "none",
                "rules",
            )
        return None

    def faq_answer(self, text: str, stop_id: str | None = None) -> str | None:
        answer, score = self._faq_match(text, stop_id)
        return answer if score >= 1.5 else None

    def fact_answer(self, text: str, stop_id: str | None) -> str | None:
        answer, score = self._fact_match(text, stop_id)
        return answer if score >= 1.5 else None

    def answer(self, text: str, stop_id: str | None) -> str | None:
        """The better of the FAQ and the stop facts, on one scale."""

        faq, faq_score = self._faq_match(text, stop_id)
        fact, fact_score = self._fact_match(text, stop_id)
        if max(faq_score, fact_score) < 1.5:
            return None
        return faq if faq_score >= fact_score else fact

    def _faq_match(self, text: str, stop_id: str | None) -> tuple[str | None, float]:
        clean = _normalize(text)
        words = _content_words(text)
        best, best_score = None, 0.0
        for item in self.profile.get("faq", []):
            keywords = [k.lower() for k in item.get("keywords", [])]
            kw_hits = sum(1 for k in keywords if _has(clean, k))
            q_words = _content_words(item.get("q", ""))
            covered = q_words | {_stem(w) for k in keywords for w in k.split()}
            overlap = len(words & q_words) / max(1, len(q_words))
            # Words the visitor used that this FAQ entry knows nothing about
            # ("how long do you DRY the za'atar") mean it answers another question.
            unmatched = len(words - covered)
            score = kw_hits + overlap * 2 - 0.35 * unmatched
            # "Can I buy some?" at the press means the oil, not the za'atar.
            hint = _STOP_HINTS.get(stop_id or "")
            if hint and kw_hits and hint in _normalize(item.get("q", "") + " " + item.get("a", "")):
                score += 0.75
            if score > best_score:
                best, best_score = item, score
        return (str(best.get("a", "")).strip() or None) if best else None, best_score

    def _fact_match(self, text: str, stop_id: str | None) -> tuple[str | None, float]:
        words = _content_words(text)
        best, best_score = None, 0.0
        if not words:
            return None, 0.0
        for stop in self.profile.get("stops", []):
            bonus = 0.5 if stop.get("id") == stop_id else 0.0
            for fact in stop.get("facts", []):
                hits = len(words & _content_words(fact))
                if hits and hits + bonus > best_score:
                    best, best_score = fact, hits + bonus
        return best, best_score

    def _follow_up(self, stop_id: str | None) -> str:
        stops = [s.get("id") for s in self.profile.get("stops", [])]
        if stops and stop_id == stops[-1] and not self._asked_highlight:
            self._asked_highlight = True
            return "What did you enjoy most today?"
        return ""

    def _next_fact(self, stop_id: str | None) -> str | None:
        for stop in self.profile.get("stops", []):
            if stop.get("id") == stop_id and stop.get("facts"):
                index = self._fact_cursor.get(stop_id, 0)
                facts = stop["facts"]
                if index >= len(facts):
                    return None
                self._fact_cursor[stop_id] = index + 1
                return facts[index]
        return None

    def _product_for(self, stop_id: str | None, topic: str | None) -> dict[str, Any] | None:
        needle = _STOP_HINTS.get(topic or "") or _STOP_HINTS.get(stop_id or "")
        if not needle:
            return None
        for product in self.profile.get("products", []):
            name = str(product.get("name", ""))
            if needle in name.lower() and name not in self._mentioned_products:
                return product
        return None


def _stem(word: str) -> str:
    if word.endswith("ied") and len(word) > 4:
        return word[:-3] + "y"  # dried -> dry
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _content_words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z']+", _normalize(text)) if w not in _STOPWORDS and len(w) > 2}


_STOP_HINTS = {
    "press": "oil", "grove": "oil", "olive_oil": "oil",
    "herbs": "za'atar", "kitchen": "za'atar", "food": "za'atar", "welcome": "sage",
}


def _from_reply(reply: dict[str, Any] | None) -> CoachResult | None:
    if not reply:
        return None
    say = str(reply.get("say") or "").strip().strip('"')
    if not say:
        return None
    next_step = str(reply.get("next_step") or "none").strip().lower()
    options = [o for o in reply.get("options", []) if isinstance(o, dict)] if isinstance(reply.get("options"), list) else []
    return CoachResult(
        options=options[:2],
        say=say[:400],
        why=str(reply.get("why") or "").strip()[:120],
        topic=(str(reply.get("topic")).strip() or None) if reply.get("topic") else None,
        next_step=next_step if next_step in NEXT_STEPS else "none",
        source="llm",
    )


def _next_step_for(tags: dict[str, Any]) -> str:
    return {"price": "buy", "buy": "buy", "booking": "book"}.get(tags.get("intent", ""), "none")
