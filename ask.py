"""Answer Noor's own questions about her business ("What do French guests ask
about most?", "Why do people complain?") from her tours and reviews.

Same two layers as the insights: a deterministic answer that always works
(stats plus the matching reviews and tour questions), and a small model that
phrases it when one is running. Every answer carries its evidence.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import languages
from engagement import _normalize
from engines.llm import LLM
from insights import aggregate_tours, analyse_reviews

_STOP = {
    "what", "which", "who", "how", "why", "when", "where", "do", "does", "did", "is", "are", "was",
    "the", "a", "an", "my", "our", "of", "to", "in", "on", "for", "and", "or", "most", "about",
    "guests", "guest", "visitors", "visitor", "people", "they", "them", "their", "me", "i", "we",
    "should", "can", "could", "would", "tell", "show", "much", "many", "more", "with", "at", "it",
    "this", "that", "there", "any", "have", "has", "get", "say", "said", "ask", "asked", "asking",
    "tours", "tour", "reviews", "review", "stop", "stops",
}

ASK_SYSTEM = (
    "You answer a small farm-tour operator's question about her own business. Use ONLY the data given: "
    "tour statistics, visitor questions from tours, and reviews with ids. Be specific and practical, in plain words, "
    "3 to 5 sentences. If the data cannot answer, say so and say what data would. Never invent numbers. "
    'Return JSON: {"answer": str, "evidence": [review ids like "R3" or "tours"]}'
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", _normalize(text)) if w not in _STOP and len(w) > 2}


def _language_mentioned(question: str) -> str | None:
    q = question.lower()
    for lang in languages.LANGUAGES.values():
        if lang.code != "en" and lang.name.lower() in q:
            return lang.code
    return None


def _stop_mentioned(question: str, profile: dict[str, Any]) -> dict[str, Any] | None:
    q = set(re.findall(r"[a-z']+", _normalize(question)))
    best, best_hits = None, 0
    for stop in profile.get("stops", []):
        name_words = _words(stop.get("name", "")) | {stop.get("id", "")}
        hits = sum(1 for w in name_words if w and w in q)
        if hits > best_hits:
            best, best_hits = stop, hits
    return best


def _all_questions(tours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for tour in tours:
        langs = {t["id"]: t.get("lang") for t in tour.get("turns", [])}
        for q in (tour.get("engagement") or {}).get("questions", []):
            out.append({**q, "lang": langs.get(q.get("turn_id")), "tour": tour.get("id")})
    return out


def _search(question: str, reviews: list[dict[str, Any]], questions: list[dict[str, Any]]) -> tuple[list, list]:
    words = _words(question)
    if not words:
        return [], []

    def score(text: str) -> int:
        tw = _words(text)
        return len(words & tw) + sum(1 for w in words for t in tw if len(w) > 4 and (t.startswith(w[:5])))

    rev = sorted(((score(r.get("text_en") or r.get("text", "")), r) for r in reviews), key=lambda x: -x[0])
    qs = sorted(((score(q.get("text", "")), q) for q in questions), key=lambda x: -x[0])
    return [r for s, r in rev if s > 0][:6], [q for s, q in qs if s > 0][:6]


def _quote(text: str, words: int = 22) -> str:
    parts = (text or "").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def answer_rules(question: str, tours: list[dict[str, Any]], reviews: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    q = _normalize(question)
    tstats = aggregate_tours(tours)
    rstats = analyse_reviews(reviews)
    questions = _all_questions(tours)
    lines: list[str] = []
    evidence: list[str] = []
    snippets: list[dict[str, str]] = []

    lang = _language_mentioned(question)
    stop = _stop_mentioned(question, profile)

    if lang:
        theirs = [x for x in questions if x.get("lang") == lang]
        name = languages.name(lang)
        if theirs:
            topics = Counter(x.get("topic") for x in theirs if x.get("topic"))
            stops = Counter(x.get("stop") for x in theirs)
            lines.append(f"{name}-speaking guests asked {len(theirs)} question(s) across your tours.")
            if topics:
                lines.append("They asked most about " + ", ".join(t.replace("_", " ") for t, _ in topics.most_common(3)) + ".")
            if stops:
                top_stop = stops.most_common(1)[0][0]
                lines.append(f"Most of their questions came at the {_stop_name(top_stop, profile)}.")
            snippets += [{"source": "tour", "text": x["text"]} for x in theirs[:4]]
            evidence.append("tours")
        else:
            lines.append(f"No questions from {name}-speaking guests are recorded yet.")
        their_reviews = [r for r in reviews if r.get("lang") == lang]
        if their_reviews:
            lines.append(f"There are {len(their_reviews)} review(s) in {name}.")
            snippets += [{"source": r["id"], "text": _quote(r.get("text_en") or r["text"])} for r in their_reviews[:3]]
            evidence += [r["id"] for r in their_reviews[:3]]

    elif stop:
        sid = stop["id"]
        row = next((s for s in tstats["questions_by_stop"] if s["id"] == sid), None)
        n = row["questions"] if row else 0
        rank = [s["id"] for s in tstats["questions_by_stop"]].index(sid) + 1 if row else None
        lines.append(
            f"The {stop['name']} drew {n} visitor question(s)"
            + (f", ranking #{rank} of {len(tstats['questions_by_stop'])} stops." if rank else ".")
        )
        here = [x for x in questions if x.get("stop") == sid]
        if here:
            snippets += [{"source": "tour", "text": x["text"]} for x in here[:5]]
            evidence.append("tours")
        rev, _ = _search(stop.get("name", ""), reviews, [])
        if rev:
            lines.append(f"{len(rev)} review(s) mention it.")
            snippets += [{"source": r["id"], "text": _quote(r.get("text_en") or r["text"])} for r in rev[:3]]
            evidence += [r["id"] for r in rev[:3]]

    elif any(w in q for w in ("complain", "improve", "fix", "bad", "wrong", "problem", "wish", "worse", "negative", "unhappy")):
        bad = rstats["complaints"][:3]
        if bad:
            lines.append("The most common complaints are about " + ", ".join(f"{c['aspect'].lower()} ({c['count']})" for c in bad) + ".")
            for c in bad:
                snippets += [{"source": x["id"], "text": x["text"]} for x in c["quotes"][:1]]
                evidence += c["ids"][:3]
        if tstats["unanswered"]:
            lines.append(f"On tours, {len(tstats['unanswered'])} question(s) had no answer ready, such as “{tstats['unanswered'][0]}”.")
            evidence.append("tours")

    elif any(w in q for w in ("like", "love", "best", "enjoy", "favourite", "favorite", "praise", "good", "positive", "value")):
        good = rstats["praised"][:3]
        if good:
            lines.append("Visitors praise " + ", ".join(f"{p['aspect'].lower()} ({p['count']})" for p in good) + " most.")
            for p in good:
                snippets += [{"source": x["id"], "text": x["text"]} for x in p["quotes"][:1]]
                evidence += p["ids"][:3]
        if tstats["questions_by_stop"]:
            top = tstats["questions_by_stop"][0]
            lines.append(f"On tours, the {top['name']} draws the most curiosity ({top['questions']} questions).")
            evidence.append("tours")

    elif any(w in q for w in ("engag", "popular", "interest", "curious", "question", "hot", "where")):
        if tstats["questions_by_stop"]:
            top = tstats["questions_by_stop"][:3]
            lines.append("Guests ask the most questions at " + ", ".join(f"the {s['name']} ({s['questions']})" for s in top) + ".")
        if tstats["questions_by_topic"]:
            lines.append("Top topics: " + ", ".join(f"{t['label'].lower()} ({t['questions']})" for t in tstats["questions_by_topic"][:3]) + ".")
        evidence.append("tours")

    elif any(w in q for w in ("unanswer", "didn't know", "did not know", "missing", "gap")):
        if tstats["unanswered"]:
            lines.append("Questions you had no answer for: " + "; ".join(f"“{u}”" for u in tstats["unanswered"][:5]) + ".")
            lines.append("Add the answers to your farm briefing and the co-pilot will use them next time.")
            evidence.append("tours")
        else:
            lines.append("No unanswered questions are recorded yet.")

    elif any(w in q for w in ("rating", "star", "score", "average")):
        if rstats["avg_rating"] is not None:
            lines.append(f"Your {rstats['review_count']} reviews average {rstats['avg_rating']} stars.")
            evidence.append("reviews")

    elif any(w in q for w in ("buy", "sell", "product", "shop", "price", "money", "revenue", "sale")):
        buy = tstats["intents"].get("buy", 0) + tstats["intents"].get("price", 0)
        lines.append(f"Guests asked about buying or prices {buy} time(s) on tours.")
        shop = next((c for c in rstats["complaints"] if c["aspect"].startswith("Shop")), None)
        if shop:
            lines.append(f"{shop['count']} review(s) complain about the shop, for example: “{shop['quotes'][0]['text']}”" if shop["quotes"] else "")
            evidence += shop["ids"][:3]
        evidence.append("tours")

    # Always add the closest matching reviews and tour questions.
    rev, qs = _search(question, reviews, questions)
    if not lines:
        if rev or qs:
            lines.append(f"I found {len(rev)} review(s) and {len(qs)} tour question(s) that match.")
        else:
            lines.append("I couldn't find anything about that in your tours or reviews yet.")
    seen = {s["text"] for s in snippets}
    for r in rev[:3]:
        text = _quote(r.get("text_en") or r["text"])
        if text not in seen:
            snippets.append({"source": r["id"], "text": text})
            evidence.append(r["id"])
    for x in qs[:3]:
        if x["text"] not in seen:
            snippets.append({"source": "tour", "text": x["text"]})
    return {
        "answer": " ".join(l for l in lines if l),
        "evidence": list(dict.fromkeys(evidence)),
        "snippets": snippets[:8],
        "source": "rules",
    }


def _stop_name(stop_id: str | None, profile: dict[str, Any]) -> str:
    return next((s.get("name", stop_id) for s in profile.get("stops", []) if s.get("id") == stop_id), stop_id or "unknown stop").lower()


async def answer(
    question: str,
    tours: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    profile: dict[str, Any],
    llm: LLM | None,
    llm_available: bool,
) -> dict[str, Any]:
    base = answer_rules(question, tours, reviews, profile)
    if llm is None or not llm_available:
        return base
    rev, qs = _search(question, reviews, _all_questions(tours))
    data = {
        "tour_stats": aggregate_tours(tours),
        "review_stats": {k: v for k, v in analyse_reviews(reviews).items() if k != "languages"},
        "matching_reviews": [{"id": r["id"], "rating": r.get("rating"), "text": _quote(r.get("text_en") or r["text"], 60)} for r in rev],
        "matching_tour_questions": [x["text"] for x in qs],
        "rule_based_answer": base["answer"],
    }
    reply = await llm.complete_json(
        ASK_SYSTEM,
        f"Business: {profile.get('business_name')}\nQuestion: {question}\n\nData:\n" + json.dumps(data, ensure_ascii=False)[:9000],
        max_tokens=400,
    )
    if reply and isinstance(reply.get("answer"), str) and reply["answer"].strip():
        evidence = reply.get("evidence") if isinstance(reply.get("evidence"), list) else base["evidence"]
        return {**base, "answer": reply["answer"].strip(), "evidence": [str(e) for e in evidence][:10], "source": "llm"}
    return base
