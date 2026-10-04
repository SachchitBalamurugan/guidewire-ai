"""Turning tours and reviews into decisions for the business.

Two layers, so the dashboard is useful with no model at all:
1. Deterministic: question counts per stop/topic across tours, unanswered
   questions, aspect-level praise and complaints from reviews (each linked to
   the review ids behind it).
2. A small model (map-reduce over review batches, because a 3B model's
   context is small) phrases the findings as recommendations, a next product
   idea, listing copy and a follow-up message. Every claim must cite ids.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from typing import Any

import languages
from engagement import NEGATIVE_TERMS, POSITIVE_TERMS, _has, _normalize
from engines.llm import LLM

ASPECTS: dict[str, tuple[str, ...]] = {
    "Food & lunch": ("food", "lunch", "meal", "cook", "maqluba", "musakhan", "dish", "delicious", "taste", "breakfast", "bread"),
    "Olive oil & press": ("oil", "press", "olive", "mill", "pressing"),
    "Herbs & za'atar": ("zaatar", "za'atar", "herb", "sage", "mint", "tea", "spice"),
    "Hosts & hospitality": ("noor", "host", "family", "mother", "welcome", "hospitality", "kind", "warm", "friendly"),
    "Storytelling & history": ("story", "stories", "history", "tradition", "generation", "learn", "explained"),
    "Scenery & setting": ("view", "views", "trees", "grove", "beautiful", "peaceful", "quiet", "landscape", "sunset"),
    "Hands-on activities": ("harvest", "pick", "picking", "workshop", "hands-on", "make", "made our", "try"),
    "Language & communication": ("english", "language", "translate", "translation", "understand", "communication", "guide"),
    "Price & value": ("price", "value", "expensive", "cheap", "worth", "cost", "money"),
    "Getting there": ("road", "find", "found", "directions", "map", "taxi", "bus", "parking", "far", "drive", "lost"),
    "Booking & contact": ("book", "booking", "whatsapp", "reply", "website", "online", "contact", "reservation"),
    "Shop & products": ("buy", "bought", "shop", "bottle", "jar", "souvenir", "gift", "products"),
    "Accessibility & comfort": ("wheelchair", "accessible", "toilet", "bathroom", "shade", "hot", "sun", "seating", "kids", "children"),
    "Length & pace": ("long", "short", "rushed", "time", "hours", "pace", "waiting"),
}

LATIN_STOPWORDS = {
    "en": {"the", "and", "was", "we", "with", "very", "is", "of", "to", "it"},
    "fr": {"le", "la", "les", "et", "très", "nous", "avec", "est", "une", "des"},
    "de": {"der", "die", "und", "das", "sehr", "wir", "mit", "ist", "ein", "nicht"},
    "es": {"el", "la", "los", "y", "muy", "con", "fue", "una", "es", "nos"},
    "it": {"il", "e", "molto", "con", "una", "abbiamo", "è", "di", "che", "la"},
    "pt": {"o", "a", "e", "muito", "com", "uma", "foi", "não", "de", "os"},
    "nl": {"de", "het", "en", "een", "zeer", "met", "we", "was", "heel", "niet"},
}


def detect_text_language(text: str) -> str:
    """Script and stopword heuristic. Reviews arrive without a language tag,
    and NLLB needs one; this is enough to route them."""

    if re.search(r"[؀-ۿ]", text):
        return "ar"
    if re.search(r"[֐-׿]", text):
        return "he"
    if re.search(r"[Ѐ-ӿ]", text):
        return "ru"
    if re.search(r"[Ͱ-Ͽ]", text):
        return "el"
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    scores = {lang: sum(1 for w in words if w in stop) for lang, stop in LATIN_STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"


# ---------- Per-tour report ----------

def build_tour_report(tour: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    engagement = tour.get("engagement") or {}
    turns = tour.get("turns", [])
    suggestions = tour.get("suggestions", [])
    stops = engagement.get("stops", [])
    ranked = sorted(stops, key=lambda s: (s.get("score", 0), s.get("questions", 0)), reverse=True)
    topic_list = engagement.get("topics", [])
    deferred = engagement.get("deferred_questions", [])
    questions = engagement.get("questions", [])
    next_steps = engagement.get("next_steps", {})
    product_suggestions = [s for s in suggestions if s.get("next_step") in {"buy", "book", "return", "referral", "review"}]

    missed = []
    for stop in stops:
        if stop.get("questions", 0) >= 2 and not any(s.get("stop") == stop["id"] for s in product_suggestions):
            missed.append(
                f"Visitors asked {stop['questions']} questions at {stop['name']}, but no product or next step was offered there."
            )

    recs: list[str] = []
    if ranked and ranked[0].get("score", 0) > 0:
        top = ranked[0]
        topic = topic_list[0]["label"] if topic_list else None
        recs.append(
            f"{top['name']} drew the most curiosity ({top['questions']} questions). Give it more time"
            + (f", and consider a paid add-on built around {topic.lower()}." if topic else ".")
        )
    quiet = [s for s in stops if s.get("turns", 0) == 0 and s.get("questions", 0) == 0]
    if quiet and len(quiet) < len(stops):
        recs.append(
            "No visitor spoke at " + ", ".join(s["name"] for s in quiet)
            + ". Add something to taste, touch or try there, or shorten it."
        )
    if deferred:
        recs.append(f"{len(deferred)} question(s) had no answer ready. Add the answers to your farm briefing so the co-pilot can use them next time.")
    if next_steps.get("buy"):
        recs.append("Visitors asked to buy. Keep products on display at the hot stop, not only at the end.")
    langs = [languages.name(code) for code in tour.get("guest_languages", [])]
    if langs:
        recs.append("Guests spoke " + ", ".join(langs) + ". Add a short translated welcome card and listing text in these languages.")
    blink = engagement.get("blink_turns", 0)
    if blink:
        recs.append(f"A guest took part through blink input ({blink} message(s)). Mention accessible tours in your listing.")

    return {
        "tour_id": tour.get("id"),
        "duration_min": _duration_min(tour),
        "guest_languages": tour.get("guest_languages", []),
        "turns": len(turns),
        "guest_turns": engagement.get("guest_turns", 0),
        "question_count": engagement.get("question_count", 0),
        "blink_turns": blink,
        "stops": ranked,
        "hot_stop": engagement.get("hot_stop_name"),
        "topics": topic_list,
        "intents": engagement.get("intents", {}),
        "unanswered": deferred,
        "top_questions": [q.get("text") for q in questions[:12]],
        "suggestions": len(suggestions),
        "suggestions_used": sum(1 for s in suggestions if s.get("used")),
        "next_step_signals": next_steps,
        "missed_moments": missed,
        "mood": engagement.get("overall_mood"),
        "recommendations": recs,
        "latency": tour.get("latency", {}),
    }


def _duration_min(tour: dict[str, Any]) -> int | None:
    from datetime import datetime

    try:
        start = datetime.fromisoformat(tour["started_at"])
        end = datetime.fromisoformat(tour["ended_at"])
        return max(1, round((end - start).total_seconds() / 60))
    except Exception:
        return None


# ---------- Reviews ----------

def analyse_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    praised: dict[str, set[str]] = defaultdict(set)
    complaints: dict[str, set[str]] = defaultdict(set)
    quotes: dict[str, list[dict[str, str]]] = defaultdict(list)
    ratings = [r["rating"] for r in reviews if isinstance(r.get("rating"), (int, float))]
    for review in reviews:
        text = review.get("text_en") or review.get("text") or ""
        rating = review.get("rating")
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            clean = _normalize(sentence)
            if not clean:
                continue
            pos = sum(1 for t in POSITIVE_TERMS if _has(clean, t))
            neg = sum(1 for t in NEGATIVE_TERMS if _has(clean, t)) + sum(
                1 for t in ("but", "wish", "could be", "should", "hard to", "no sign", "didn't", "wasn't", "too ") if t in clean
            )
            if pos == neg and isinstance(rating, (int, float)):
                pos += rating >= 4
                neg += rating <= 2
            for aspect, keywords in ASPECTS.items():
                if not any(_has(clean, k) for k in keywords):
                    continue
                bucket = praised if pos > neg else complaints if neg > pos else None
                if bucket is None:
                    continue
                bucket[aspect].add(review["id"])
                if len(quotes[aspect]) < 3:
                    quotes[aspect].append({"id": review["id"], "text": _clip(sentence, 24)})
    def table(source: dict[str, set[str]]) -> list[dict[str, Any]]:
        return [
            {"aspect": aspect, "count": len(ids), "ids": sorted(ids, key=_id_num), "quotes": quotes.get(aspect, [])}
            for aspect, ids in sorted(source.items(), key=lambda item: -len(item[1]))
        ]

    return {
        "review_count": len(reviews),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "languages": dict(Counter(r.get("lang") or "en" for r in reviews)),
        "praised": table(praised),
        "complaints": table(complaints),
    }


def aggregate_tours(tours: list[dict[str, Any]]) -> dict[str, Any]:
    by_stop: Counter = Counter()
    stop_names: dict[str, str] = {}
    by_topic: Counter = Counter()
    topic_labels: dict[str, str] = {}
    intents: Counter = Counter()
    deferred: list[str] = []
    langs: Counter = Counter()
    steps: Counter = Counter()
    blink = 0
    questions = 0
    for tour in tours:
        engagement = tour.get("engagement") or {}
        for stop in engagement.get("stops", []):
            by_stop[stop["id"]] += stop.get("questions", 0)
            stop_names[stop["id"]] = stop.get("name", stop["id"])
        for topic in engagement.get("topics", []):
            by_topic[topic["id"]] += topic.get("questions", 0)
            topic_labels[topic["id"]] = topic.get("label", topic["id"])
        intents.update(engagement.get("intents", {}))
        steps.update(engagement.get("next_steps", {}))
        deferred.extend(engagement.get("deferred_questions", []))
        langs.update(tour.get("guest_languages", []))
        blink += engagement.get("blink_turns", 0)
        questions += engagement.get("question_count", 0)
    return {
        "tour_count": len(tours),
        "question_count": questions,
        "questions_by_stop": [
            {"id": sid, "name": stop_names.get(sid, sid), "questions": n} for sid, n in by_stop.most_common()
        ],
        "questions_by_topic": [
            {"id": tid, "label": topic_labels.get(tid, tid), "questions": n} for tid, n in by_topic.most_common() if n
        ],
        "intents": dict(intents.most_common()),
        "next_step_signals": dict(steps),
        "unanswered": list(dict.fromkeys(deferred))[:20],
        "guest_languages": {languages.name(k): v for k, v in langs.most_common()},
        "blink_turns": blink,
    }


def rule_recommendations(review_stats: dict[str, Any], tour_stats: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    keep = [
        {"point": f"Visitors keep praising {p['aspect'].lower()}.", "evidence": p["ids"][:6]}
        for p in review_stats.get("praised", [])[:3]
    ]
    fix = [
        {"point": f"Recurring complaint about {c['aspect'].lower()}.", "evidence": c["ids"][:6]}
        for c in review_stats.get("complaints", [])[:3]
    ]
    for question in tour_stats.get("unanswered", [])[:3]:
        fix.append({"point": f"Have an answer ready for: \"{question}\"", "evidence": ["tour transcripts"]})
    ideas = []
    topics = tour_stats.get("questions_by_topic", [])
    praised = [p["aspect"] for p in review_stats.get("praised", [])]
    if topics:
        ideas.append(
            {
                "idea": f"A paid add-on built around {topics[0]['label'].lower()}",
                "why": f"Most-asked topic on tours ({topics[0]['questions']} question{'s' if topics[0]['questions'] != 1 else ''}).",
                "evidence": ["tour transcripts"],
            }
        )
    if "Hands-on activities" in praised:
        ideas.append({"idea": "Seasonal hands-on day (harvest or workshop) sold ahead", "why": "Hands-on moments are the most praised.", "evidence": next(p["ids"][:5] for p in review_stats["praised"] if p["aspect"] == "Hands-on activities")})
    business = profile.get("business_name", "our farm")
    loved = ", ".join(p["aspect"].lower() for p in review_stats.get("praised", [])[:2]) or "a slow, real day on a family farm"
    listing = (
        f"{business} in {profile.get('location', '')}: {profile.get('description', '')} "
        f"Guests especially love {loved}."
    ).strip()
    follow_up = (
        f"Thank you for visiting {business} today. It was a pleasure to host you. "
        "If you enjoyed it, a short review helps a small family farm more than you know. "
        "We'd love to see you again at the olive harvest, and your friends are always welcome."
    )
    return {"keep_doing": keep, "fix": fix, "next_products": ideas, "listing_copy": listing, "follow_up_message": follow_up}


MAP_SYSTEM = (
    "You analyse visitor reviews for a small family farm-tour business. Use only what the reviews say. "
    "Return JSON: {\"loved\": [{\"point\": str, \"ids\": [review ids]}], \"wished\": [{\"point\": str, \"ids\": [...]}], "
    "\"ideas\": [{\"idea\": str, \"ids\": [...]}]}. Max 4 items per list. Every item must cite review ids from the input."
)

REDUCE_SYSTEM = (
    "You are a business advisor for a small farm-tour operator who has no time for analysis. "
    "Combine the review findings and tour question data into practical advice. Never invent facts, prices or numbers. "
    "Return JSON: {\"keep_doing\": [{\"point\": str, \"evidence\": [ids or 'tours']}], "
    "\"fix\": [{\"point\": str, \"evidence\": [...]}], "
    "\"next_products\": [{\"idea\": str, \"why\": str, \"evidence\": [...]}], "
    "\"listing_copy\": str (2-3 sentences for a booking listing), "
    "\"follow_up_message\": str (a warm 3-sentence WhatsApp message to send guests after a tour, asking for a review)}. "
    "Max 4 items per list; plain words."
)


async def build_business_insights(
    tours: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    profile: dict[str, Any],
    llm: LLM | None,
    llm_available: bool,
) -> dict[str, Any]:
    review_stats = analyse_reviews(reviews)
    tour_stats = aggregate_tours(tours)
    base = rule_recommendations(review_stats, tour_stats, profile)
    result: dict[str, Any] = {
        "reviews": review_stats,
        "tours": tour_stats,
        "recommendations": base,
        "source": "rules",
    }
    if llm is None or not llm_available or (not reviews and not tours):
        return result

    findings: list[dict[str, Any]] = []
    batch = 8
    for start in range(0, len(reviews), batch):
        chunk = reviews[start : start + batch]
        user = "\n".join(
            f"[{r['id']}] ({r.get('rating') or '?'} stars) {_clip(r.get('text_en') or r.get('text', ''), 120)}" for r in chunk
        )
        reply = await llm.complete_json(MAP_SYSTEM, user, max_tokens=500)
        if reply:
            findings.append(reply)
    summary = {
        "review_findings": findings,
        "review_aspects_praised": [(p["aspect"], p["count"]) for p in review_stats["praised"][:6]],
        "review_aspects_complaints": [(c["aspect"], c["count"]) for c in review_stats["complaints"][:6]],
        "tour_questions_by_stop": tour_stats["questions_by_stop"][:6],
        "tour_questions_by_topic": tour_stats["questions_by_topic"][:6],
        "unanswered_tour_questions": tour_stats["unanswered"][:6],
        "guest_languages": tour_stats["guest_languages"],
        "products": [p.get("name") for p in profile.get("products", [])],
        "offers": profile.get("offers", []),
    }
    import json

    reply = await llm.complete_json(
        REDUCE_SYSTEM,
        f"Business: {profile.get('business_name')} - {profile.get('description')}\n\nData:\n"
        + json.dumps(summary, ensure_ascii=False),
        max_tokens=900,
    )
    merged = _merge_recommendations(base, reply)
    if merged is not base:
        result["recommendations"] = merged
        result["source"] = "llm"
    return result


def _merge_recommendations(base: dict[str, Any], reply: dict[str, Any] | None) -> dict[str, Any]:
    if not reply:
        return base
    out = dict(base)
    for key in ("keep_doing", "fix", "next_products"):
        items = reply.get(key)
        if isinstance(items, list) and items:
            cleaned = [i for i in items if isinstance(i, dict) and (i.get("point") or i.get("idea"))]
            if cleaned:
                out[key] = cleaned[:4]
    for key in ("listing_copy", "follow_up_message"):
        if isinstance(reply.get(key), str) and reply[key].strip():
            out[key] = reply[key].strip()
    return out


async def translate_reviews(reviews: list[dict[str, Any]], translator: Any, pivot: str = "en") -> bool:
    """Fill lang/text_en on reviews that lack them. Returns True if any changed."""

    changed = False
    for review in reviews:
        if review.get("text_en"):
            continue
        lang = languages.normalize(review.get("lang")) or detect_text_language(review["text"])
        if review.get("lang") != lang:
            review["lang"] = lang
            changed = True
        if lang == pivot:
            review["text_en"] = review["text"]
            changed = True
            continue
        try:
            out = await asyncio.to_thread(translator.translate, review["text"], lang, pivot)
        except Exception:
            continue
        # Without NLLB the "translation" is the original; leave text_en empty
        # so the review is translated properly once the model is available.
        if getattr(translator, "ready", False) and out:
            review["text_en"] = out
            changed = True
    return changed


def _clip(text: str, words: int) -> str:
    parts = (text or "").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _id_num(review_id: str) -> int:
    digits = re.sub(r"\D", "", review_id or "")
    return int(digits) if digits else 0
