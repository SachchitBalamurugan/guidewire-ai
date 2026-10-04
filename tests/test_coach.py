import asyncio

import storage
from coach import Coach, Turn, format_briefing
from engagement import TourEngagement
from engines.llm import FakeLLM, RulesLLM


def guest_turn(text, stop="welcome"):
    e = TourEngagement.from_profile(storage.get_profile())
    e.set_stop(stop)
    tags = e.ingest_guest("t1", text)
    return Turn("guest", text, "voice", tags)


def test_briefing_contains_facts_and_limits():
    briefing = format_briefing(storage.get_profile())
    assert "8 JOD" in briefing
    assert "Never: Do not call the oil organic" in briefing


def test_rules_answer_from_faq():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("How old are the trees?", "grove"), "grove")
    assert "300 years" in result.say
    assert result.source == "rules"


def test_rules_admit_unknowns_instead_of_inventing():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("What kind of soil do you have?", "grove"), "grove")
    assert "I'll check" in result.say


def test_rules_offer_product_once_on_delight():
    coach = Coach(RulesLLM(), storage.get_profile())
    first = coach.rules(guest_turn("This oil is delicious.", "press"), "press")
    assert "olive oil" in first.say.lower() and first.next_step == "buy"
    second = coach.rules(guest_turn("Really delicious oil.", "press"), "press")
    assert "500 ml" not in second.say or second.say != first.say


def test_rules_respond_to_fatigue():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("I'm a bit tired, it's hot."), "herbs")
    assert "shade" in result.say


def test_llm_reply_is_used_and_prompt_has_context():
    llm = FakeLLM([{"say": "It's sage tea from our garden.", "why": "Answer", "topic": "herbs", "next_step": "none"}])
    coach = Coach(llm, storage.get_profile())
    result = asyncio.run(coach.suggest([guest_turn("What is this tea?")], "welcome", "Welcome & tea", "Herbs"))
    assert result.say == "It's sage tea from our garden."
    assert result.source == "llm" and result.topic == "herbs"
    system, user = llm.prompts[0]
    assert "Welcome & tea" in system and "8 JOD" in system
    assert "Visitor: What is this tea?" in user


def test_llm_failure_falls_back_to_rules():
    coach = Coach(FakeLLM([None]), storage.get_profile())
    result = asyncio.run(coach.suggest([guest_turn("Is there a toilet?")], "welcome", "Welcome"))
    assert result.source == "rules" and "toilet" in result.say.lower()


def test_no_update_is_recognised():
    coach = Coach(FakeLLM([{"say": "NO_UPDATE"}]), storage.get_profile())
    result = asyncio.run(coach.suggest([guest_turn("Haha.")], "welcome", "Welcome"))
    assert result.no_update


def test_rules_respect_claims_not_to_make():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("Is the oil organic?", "press"), "press")
    assert "isn't certified organic" in result.say


def test_rules_use_the_current_stop_to_pick_the_product():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("Can we buy some?", "press"), "press")
    assert "500 ml" in result.say


def test_rules_fall_back_to_stop_facts_before_admitting_unknowns():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("How long do you dry the za'atar?", "herbs"), "herbs")
    assert "ten days" in result.say


def test_rules_turn_a_wish_to_return_into_a_booking_step():
    coach = Coach(RulesLLM(), storage.get_profile())
    result = coach.rules(guest_turn("I would like to come back again."), "kitchen")
    assert "WhatsApp" in result.say and result.next_step == "return"
