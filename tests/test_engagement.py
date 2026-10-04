import storage
from engagement import TourEngagement, classify_intent, is_question


def make():
    return TourEngagement.from_profile(storage.get_profile())


def test_question_detection():
    assert is_question("How old are the trees?")
    assert is_question("how much is the oil")
    assert is_question("Est-ce que vous arrosez ?")
    assert not is_question("This is wonderful.")
    assert not is_question("")


def test_intents():
    assert classify_intent("How much does a bottle cost?") == "price"
    assert classify_intent("Where is the toilet?") == "directions"
    assert classify_intent("Can we come back for the harvest with friends?") == "booking"
    assert classify_intent("Is the lunch vegetarian?") == "food"


def test_questions_are_credited_to_the_current_stop_and_topic():
    e = make()
    assert e.current_stop == "welcome"
    e.set_stop("press")
    e.ingest_guest("t1", "How many olives make a litre of oil?")
    e.ingest_guest("t2", "Is the oil cold pressed?")
    e.set_stop("herbs")
    e.ingest_guest("t3", "What is in za'atar?")
    e.ingest_guest("t4", "Lovely garden.")
    data = e.to_json()
    by_stop = {s["id"]: s["questions"] for s in data["stops"]}
    assert by_stop["press"] == 2
    assert by_stop["herbs"] == 1
    assert data["question_count"] == 3
    assert data["hot_stop"] == "press"
    assert data["hot_topic"] == "olive_oil"


def test_unknown_stop_is_ignored():
    e = make()
    e.set_stop("nowhere")
    assert e.current_stop == "welcome"


def test_deferred_answers_are_listed_as_unanswered():
    e = make()
    e.ingest_guest("t1", "Is the oil organic?")
    assert e.ingest_guide("I'm not sure, I'll check.") is True
    e.ingest_guest("t2", "How old are the trees?")
    assert e.ingest_guide("Over three hundred years.") is False
    assert e.to_json()["deferred_questions"] == ["Is the oil organic?"]


def test_blink_turns_and_next_steps():
    e = make()
    e.ingest_guest("t1", "I would like to come back again.", source="blink")
    e.ingest_guest("t2", "Can I buy a bottle?")
    data = e.to_json()
    assert data["blink_turns"] == 1
    assert data["next_steps"]["return"] == 1
    assert data["next_steps"]["buy"] == 1


def test_retag_topic_moves_the_count():
    e = make()
    e.ingest_guest("t1", "Can you tell me about this?")
    e.retag_topic("t1", "history")
    assert {t["id"]: t["questions"] for t in e.to_json()["topics"]} == {"history": 1}
