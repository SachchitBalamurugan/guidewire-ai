import asyncio

import storage
from engines.llm import FakeLLM
from insights import aggregate_tours, analyse_reviews, build_business_insights, detect_text_language


def reviews():
    items = storage.add_reviews(storage.sample_reviews())
    for r in items:
        r["text_en"] = r["text"]
    return items


def test_sample_reviews_parse():
    items = storage.sample_reviews()
    assert len(items) == 15
    assert items[0]["rating"] == "5"


def test_paste_parsing_blocks():
    items = storage.parse_reviews("Great lunch.\n\nHard to find the farm.")
    assert [i["text"] for i in items] == ["Great lunch.", "Hard to find the farm."]


def test_language_detection():
    assert detect_text_language("تجربة رائعة") == "ar"
    assert detect_text_language("Un accueil chaleureux et un déjeuner délicieux.") == "fr"
    assert detect_text_language("Wir durften beim Pressen helfen, das war sehr schön") == "de"
    assert detect_text_language("The lunch was great") == "en"


def test_review_aspects_cite_ids():
    stats = analyse_reviews(reviews())
    praised = {p["aspect"]: p for p in stats["praised"]}
    complaints = {c["aspect"]: c for c in stats["complaints"]}
    assert "Food & lunch" in praised and praised["Food & lunch"]["ids"]
    assert "Getting there" in complaints
    assert all(i.startswith("R") for i in complaints["Getting there"]["ids"])
    assert stats["avg_rating"] > 4


def test_rules_insights_without_model():
    data = asyncio.run(build_business_insights([], reviews(), storage.get_profile(), None, False))
    rec = data["recommendations"]
    assert data["source"] == "rules"
    assert rec["keep_doing"] and rec["fix"]
    assert "review" in rec["follow_up_message"].lower()


def test_llm_insights_merge():
    llm = FakeLLM(
        [{"loved": [{"point": "Lunch", "ids": ["R1"]}]}, {"loved": []},
         {"keep_doing": [{"point": "Keep the courtyard lunch", "evidence": ["R1"]}],
          "next_products": [{"idea": "Za'atar workshop", "why": "Asked for", "evidence": ["R10"]}],
          "listing_copy": "A family olive farm.", "follow_up_message": "Thank you for coming."}]
    )
    data = asyncio.run(build_business_insights([], reviews(), storage.get_profile(), llm, True))
    rec = data["recommendations"]
    assert data["source"] == "llm"
    assert rec["keep_doing"][0]["point"] == "Keep the courtyard lunch"
    assert rec["fix"]  # rules kept where the model said nothing
    assert len(llm.prompts) == 3  # two map batches + reduce


def test_aggregate_tours():
    tour = {
        "guest_languages": ["fr"],
        "engagement": {
            "stops": [{"id": "press", "name": "Press", "questions": 3}],
            "topics": [{"id": "olive_oil", "label": "Oil", "questions": 3}],
            "intents": {"price": 1},
            "deferred_questions": ["Is it organic?"],
            "blink_turns": 1,
            "question_count": 3,
        },
    }
    agg = aggregate_tours([tour, tour])
    assert agg["questions_by_stop"][0]["questions"] == 6
    assert agg["unanswered"] == ["Is it organic?"]
    assert agg["guest_languages"] == {"French": 2}
