import asyncio

import storage
from ask import answer, answer_rules
from engines.llm import FakeLLM


def reviews():
    items = storage.add_reviews(storage.sample_reviews())
    for r in items:
        r["text_en"] = r["text"]
    r10 = next(r for r in items if r["text"].startswith("تجربة"))
    r10["lang"], r10["text_en"] = "ar", "Great experience. We wish there was a workshop to learn to make za'atar."
    return items


def tour():
    return {
        "id": "t1",
        "guest_languages": ["fr"],
        "turns": [{"id": "t2", "lang": "fr"}, {"id": "t3", "lang": "de"}],
        "engagement": {
            "stops": [{"id": "press", "name": "Stone olive press", "questions": 3}, {"id": "grove", "name": "Olive grove", "questions": 1}],
            "topics": [{"id": "olive_oil", "label": "Olive oil & pressing", "questions": 3}],
            "intents": {"price": 1, "buy": 1},
            "deferred_questions": ["Can you ship to France?"],
            "question_count": 4,
            "questions": [
                {"turn_id": "t2", "text": "How many olives make a litre of oil?", "stop": "press", "topic": "olive_oil", "intent": "how_made"},
                {"turn_id": "t3", "text": "Is the oil organic?", "stop": "press", "topic": "olive_oil", "intent": "other"},
            ],
        },
    }


def test_language_question_uses_that_languages_turns():
    a = answer_rules("What do French guests ask about most?", [tour()], reviews(), storage.get_profile())
    assert "French-speaking guests asked 1 question" in a["answer"]
    assert "olive oil" in a["answer"]


def test_complaints_cite_reviews():
    a = answer_rules("What should I fix? What do people complain about?", [tour()], reviews(), storage.get_profile())
    assert "complaints" in a["answer"] and "getting there" in a["answer"]
    assert any(e.startswith("R") for e in a["evidence"])
    assert "Can you ship to France?" in a["answer"]


def test_stop_question():
    a = answer_rules("How is the olive press doing?", [tour()], reviews(), storage.get_profile())
    assert "Stone olive press drew 3" in a["answer"]


def test_free_text_search_finds_matching_reviews():
    a = answer_rules("anything about wheelchair access?", [tour()], reviews(), storage.get_profile())
    assert any("wheelchair" in s["text"].lower() for s in a["snippets"])


def test_model_phrases_the_answer_when_available():
    llm = FakeLLM([{"answer": "Mostly the oil.", "evidence": ["R6", "tours"]}])
    a = asyncio.run(answer("What do they ask?", [tour()], reviews(), storage.get_profile(), llm, True))
    assert a["answer"] == "Mostly the oil." and a["source"] == "llm" and a["evidence"] == ["R6", "tours"]


def test_ask_endpoint_and_transcribe_guard():
    from fastapi.testclient import TestClient
    from app import app

    with TestClient(app) as client:
        storage.add_reviews(storage.sample_reviews())
        res = client.post("/api/ask", json={"question": "What do visitors love?", "lang": "en"})
        assert res.status_code == 200 and "praise" in res.json()["answer"]
        assert client.post("/api/transcribe", content=b"\x00\x00" * 100).json() == {"text": "", "lang": None}
