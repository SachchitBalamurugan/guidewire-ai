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
    "be", "with", "my", "me", "have", "has", "some", "any", "much", "many", "really", "very",
    "please", "just", "also", "would", "like", "could", "will", "get", "one", "our", "they",
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
    # What the coach read from the moment, shown on the card ("Quiet group at Olive grove").
    situation: str = ""

    @property
    def no_update(self) -> bool:
        return self.say.strip().upper().strip('"') == "NO_UPDATE"


@dataclass
class Turn:
    speaker: str  # "guide" | "guest"
    text_en: str
    source: str = "voice"
    tags: dict[str, Any] = field(default_factory=dict)


@dataclass
class Situation:
    """Where the tour is right now, built by the session from engagement data.
    The coach picks its line from this, not from the last sentence alone."""

    stop_id: str | None = None
    stop_name: str = ""
    stop_index: int = 0
    stop_count: int = 0
    next_stop_name: str | None = None
    seconds_at_stop: float = 0.0
    questions_here: int = 0
    guest_turns_here: int = 0
    # Noor's lines in a row with no visitor in between: she is lecturing.
    guide_streak: int = 0
    hot_topic: str | None = None
    tired: bool = False
    delighted: bool = False
    blink_guest: bool = False
    unanswered: int = 0
    # guest_question | guest_statement | guide_answer | guide_deferred | guide_statement
    last_kind: str = ""

    @property
    def phase(self) -> str:
        if self.stop_count and self.stop_index >= self.stop_count - 1:
            return "closing"
        if self.stop_index == 0:
            return "opening"
        return "middle"

    @property
    def quiet(self) -> bool:
        return self.guest_turns_here == 0 and (self.seconds_at_stop > 45 or self.guide_streak >= 2)

    def label(self) -> str:
        where = self.stop_name or "this stop"
        if self.tired:
            return f"Guests seem tired at {where}"
        if self.last_kind == "guide_deferred":
            return "You owe them an answer: keep it moving"
        if self.phase == "closing" and self.last_kind.startswith("guide"):
            return "Wrapping up the tour"
        if self.quiet:
            return f"Quiet group at {where}"
        if self.questions_here >= 3:
            return f"Very curious at {where} ({self.questions_here} questions)"
        if self.delighted:
            return f"Guests are delighted at {where}"
        if self.guide_streak >= 2:
            return "You've been talking a while"
        if self.phase == "opening":
            return "Opening the tour"
        return f"At {where}"

    def describe(self) -> str:
        """For the model: the same reading, as plain facts."""

        bits = [
            f"Stop {self.stop_index + 1} of {self.stop_count}: {self.stop_name} ({self.phase} of the tour)",
            f"{int(self.seconds_at_stop // 60)} min at this stop, {self.questions_here} visitor questions here",
        ]
        if self.next_stop_name:
            bits.append(f"Next stop: {self.next_stop_name}")
        if self.hot_topic:
            bits.append(f"Most-asked topic so far: {self.hot_topic}")
        if self.guide_streak >= 2:
            bits.append(f"Noor has spoken {self.guide_streak} times in a row without visitors joining in")
        if self.tired:
            bits.append("A visitor sounds tired or uncomfortable")
        if self.delighted:
            bits.append("Visitors are clearly enjoying this stop")
        if self.blink_guest:
            bits.append("One visitor communicates by blinking: give them yes/no questions and time")
        if self.unanswered:
            bits.append(f"{self.unanswered} question(s) still owed an answer")
        bits.append(f"Last turn: {self.last_kind.replace('_', ' ')}")
        return "\n".join(f"- {b}" for b in bits)


def _as_situation(value: "Situation | str | None", stop_name: str | None = None, hot_topic: str | None = None) -> Situation:
    if isinstance(value, Situation):
        return value
    return Situation(stop_id=value, stop_name=stop_name or (value or ""), hot_topic=hot_topic)


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
        # A question saved without an answer yet is a reminder, not a fact.
        if str(item.get("a") or "").strip():
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
        self._closing_step = 0
        # Lines already suggested, so the coach moves on instead of repeating.
        self._suggested: set[str] = set()
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
        situation: "Situation | str | None",
        stop_name: str | None = None,
        hot_topic: str | None = None,
        use_llm: bool = True,
    ) -> CoachResult | None:
        """A line for whoever spoke last: an answer after a visitor, the next
        move after Noor."""

        if not turns:
            return None
        sit = _as_situation(situation, stop_name, hot_topic)
        last = turns[-1]
        self.observe(last)
        if use_llm:
            user = self._user_prompt(turns, sit)
            reply = await self.llm.complete_json(self.system_prompt(sit.stop_name), user, max_tokens=360)
            self.llm_ok = reply is not None
            result = _from_reply(reply)
            if result is not None:
                result.situation = sit.label()
                return self.with_options(result, last, sit)
        return self.rules(last, sit)

    def _user_prompt(self, turns: list[Turn], sit: Situation) -> str:
        lines = []
        for turn in turns[-12:]:
            who = "Noor" if turn.speaker == "guide" else "Visitor"
            mark = " [blink]" if turn.source == "blink" else ""
            lines.append(f"{who}{mark}: {turn.text_en}")
        ask = (
            "A visitor just spoke. What should Noor say back?"
            if turns[-1].speaker == "guest"
            else "Noor just spoke. What should she say or ask next to keep the visitors engaged and move toward a good outcome?"
        )
        return (
            f"The situation right now:\n{sit.describe()}\n\nRecent conversation (translated to English):\n"
            + "\n".join(lines)
            + f"\n\n{ask} Reply with the JSON only."
        )

    # ---------- Rule layer ----------

    def observe(self, turn: Turn) -> None:
        """Learn from what Noor actually said: a product she already priced is
        not pitched again."""

        if turn.speaker != "guide":
            return
        text = _normalize(turn.text_en)
        for product in self.profile.get("products", []):
            price = _normalize(str(product.get("price", "")))
            if price and price in text:
                self._mentioned_products.add(product["name"])

    def rules(self, last: Turn, situation: "Situation | str | None") -> CoachResult | None:
        sit = _as_situation(situation)
        if last.speaker == "guide":
            result = self._after_guide(sit)
        else:
            result = self._primary_rule(last, sit.stop_id)
            if result is not None:
                result = self._adapt_guest(result, last, sit)
        if result is None:
            return None
        result.situation = result.situation or sit.label()
        result = self.with_options(result, last, sit)
        self._suggested.add(_normalize(result.say))
        return result

    def with_options(self, result: CoachResult, last: Turn, situation: "Situation | str | None") -> CoachResult:
        """Give Noor a choice: the suggested line, a way to go deeper, and a
        gentle next step. Model-written options are kept; gaps are filled from
        the briefing."""

        sit = _as_situation(situation)
        stop_id = sit.stop_id
        if result.no_update:
            return result
        if last.speaker == "guide":
            label = "Suggested"
        else:
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
                seen.add(_normalize(step))
        if sit.blink_guest and len(options) < 4:
            yes_no = self._yes_no(sit.stop_id)
            if yes_no and _normalize(yes_no) not in seen:
                options.append({"label": "For the blink guest", "say": yes_no})
        result.options = _dedupe(options)
        return result

    # ---------- Situational rules ----------

    def _stop(self, stop_id: str | None) -> dict[str, Any]:
        return next((s for s in self.profile.get("stops", []) if s.get("id") == stop_id), {})

    def _peek_fact(self, stop_id: str | None) -> str | None:
        facts = self._stop(stop_id).get("facts", [])
        index = self._fact_cursor.get(stop_id or "", 0)
        return facts[index] if index < len(facts) else None

    def _engage(self, stop_id: str | None) -> str:
        return self._stop(stop_id).get("engage") or "What would you like to know about this?"

    def _activity(self, stop_id: str | None) -> str | None:
        activity = self._stop(stop_id).get("activity")
        return f"Would anyone like to {activity}?" if activity else None

    def _yes_no(self, stop_id: str | None) -> str | None:
        activity = self._stop(stop_id).get("activity")
        if activity:
            return f"Would you like to {activity}? You can blink YES or NO."
        return "Are you enjoying it so far? You can blink YES or NO."

    def _move_on(self, sit: Situation) -> str | None:
        if not sit.next_stop_name:
            return None
        return f"Shall we head to the {sit.next_stop_name.lower()}? There's something there I think you'll love."

    def _closing_line(self) -> tuple[str, str, str]:
        """Highlight, then review, then an invitation back, once each."""

        offer = next((o for o in self.profile.get("offers", []) if "harvest" in o.lower()), None)
        steps = [
            ("What was your favourite part of today?", "Ask for the highlight: it tells you what to build on", "none"),
            (
                "If you enjoyed today, a short review would mean a lot to a small family farm like ours.",
                "Reviews are how new guests find you",
                "review",
            ),
            (
                "We'd love to see you again." + (f" {offer}" if offer else "") + " Your friends are always welcome too.",
                "Turn a happy guest into a return visit or referral",
                "return",
            ),
        ]
        step = steps[min(self._closing_step, len(steps) - 1)]
        self._closing_step += 1
        return step

    def _after_guide(self, sit: Situation) -> CoachResult | None:
        """Noor just spoke. Suggest the next move for this exact moment."""

        stop_id = sit.stop_id
        alternatives: list[dict[str, str]] = []

        def alt(label: str, say: str | None) -> None:
            if say:
                alternatives.append({"label": label, "say": say})

        if sit.tired:
            say, why, step = "Let's take a short break in the shade. Would anyone like water or some sage tea?", "Comfort first: tired guests stop listening", "none"
            alt("Move on", self._move_on(sit))
        elif sit.last_kind == "guide_deferred":
            activity = self._stop(stop_id).get("activity")
            say = f"While I find that out for you, would you like to {activity}?" if activity else self._engage(stop_id)
            why, step = "Keep the energy up while you owe an answer", "none"
            alt("Share a fact", self._peek_fact(stop_id))
        elif sit.phase == "closing" and (sit.seconds_at_stop > 20 or sit.guest_turns_here >= 1):
            say, why, step = self._closing_line()
            alt("Invite them back", self._next_step_line(stop_id, None))
        elif sit.quiet or sit.guide_streak >= 2:
            say, why, step = self._engage(stop_id), "Hand the conversation to them: questions show you what they value", "none"
            alt("Try something", self._activity(stop_id))
            alt("Share a fact", self._peek_fact(stop_id))
        elif sit.last_kind == "guide_answer" and sit.questions_here >= 2 and self._product_for(stop_id, None):
            say, why, step = (
                self._next_step_line(stop_id, None) or self._engage(stop_id),
                "They keep asking here: the right moment to mention the product once",
                "buy",
            )
            alt("Go deeper", self._peek_fact(stop_id))
        elif sit.seconds_at_stop > 300 and sit.next_stop_name:
            say, why, step = self._move_on(sit), "You've been here a while: keep the tour moving", "none"
            alt("One more question", self._engage(stop_id))
        elif sit.phase == "opening" and sit.guest_turns_here == 0:
            say, why, step = self._engage(stop_id), "Learn who they are before you start", "none"
            alt("Try something", self._activity(stop_id))
        else:
            fact = self._peek_fact(stop_id)
            if fact and sit.last_kind != "guide_answer":
                say, why, step = f"Here's something most people don't know: {fact}", "Keep the story going with a surprise", "none"
                self._fact_cursor[stop_id or ""] = self._fact_cursor.get(stop_id or "", 0) + 1
                alt("Invite them in", self._engage(stop_id))
            else:
                say, why, step = self._engage(stop_id), "Follow up: invite their questions", "none"
                alt("Try something", self._activity(stop_id))
            alt("Move on", self._move_on(sit))
        if not say:
            return None
        if _normalize(say) in self._suggested:
            fresh = [a for a in alternatives if _normalize(a["say"]) not in self._suggested]
            if fresh:
                alternatives.remove(fresh[0])
                alternatives.append({"label": "Again", "say": say})
                say = fresh[0]["say"]
                why = f"{fresh[0]['label']}: you already used the other line"
            elif sit.next_stop_name:
                say, why = self._move_on(sit), "You've covered this stop: keep the tour moving"
        if step == "buy":
            product = self._product_for(stop_id, None)
            if product:
                self._mentioned_products.add(product["name"])
        return CoachResult(say, why, None, step, "rules", options=alternatives[:2], situation=sit.label())

    def _adapt_guest(self, result: CoachResult, last: Turn, sit: Situation) -> CoachResult:
        """Tune an answer to the moment it lands in."""

        if last.source == "blink":
            # They hear normally; it is replying that is slow. Answer fully,
            # then offer a question they can answer with one blinked word.
            result.why = "Blink guest: answer fully, follow up with a YES/NO question"
        elif sit.phase == "closing" and (last.tags or {}).get("mood") == "positive" and result.next_step == "none":
            line, _why, _step = self._closing_line()
            result.options = [{"label": "Close well", "say": line}] + result.options
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
            if not str(item.get("a") or "").strip():
                continue
            keywords = [k.lower() for k in item.get("keywords", [])]
            kw_hits = sum(1 for k in keywords if _has(clean, k))
            q_words = _content_words(item.get("q", ""))
            covered = q_words | {_stem(w) for k in keywords for w in k.split()}
            overlap = len(words & q_words) / max(1, len(q_words))
            # Words the visitor used that this FAQ entry knows nothing about
            # ("how long do you DRY the za'atar") mean it answers another question.
            unmatched = len(words - covered - _SENTIMENT)
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
            # The highlight question is the first closing step; next comes the review.
            self._closing_step = max(self._closing_step, 1)
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
        needle = _STOP_HINTS.get(stop_id or "") or _STOP_HINTS.get(topic or "")
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


_SENTIMENT = {_stem(w) for w in POSITIVE_TERMS | NEGATIVE_TERMS if " " not in w}


_STOP_HINTS = {
    "press": "oil", "grove": "oil", "olive_oil": "oil",
    "herbs": "za'atar", "kitchen": "za'atar", "food": "za'atar", "welcome": "sage",
}


def _dedupe(options: list[dict[str, str]]) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    for option in options:
        norm = _normalize(option["say"])
        if any(norm in _normalize(k["say"]) or _normalize(k["say"]) in norm for k in kept):
            continue
        kept.append(option)
    return kept


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
