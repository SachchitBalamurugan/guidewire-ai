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


from coach import Situation


def guide_turn(text):
    return Turn("guide", text, "voice", {})


def sit(**kw):
    base = dict(stop_id="grove", stop_name="Olive grove", stop_index=1, stop_count=5, next_stop_name="Stone olive press")
    base.update(kw)
    return Situation(**base)


def test_after_guide_quiet_group_gets_invited_in():
    coach = Coach(RulesLLM(), storage.get_profile())
    r = coach.rules(guide_turn("These are our olive trees."), sit(guide_streak=2, last_kind="guide_statement"))
    assert r.say == "Do any of you grow fruit trees or olives at home?"
    assert r.situation.startswith("Quiet group")
    assert any(o["label"] == "Try something" for o in r.options)


def test_after_guide_deferral_keeps_things_moving():
    coach = Coach(RulesLLM(), storage.get_profile())
    r = coach.rules(guide_turn("I'll check."), sit(stop_id="press", stop_name="Stone olive press", last_kind="guide_deferred", guest_turns_here=1))
    assert r.say.startswith("While I find that out for you")


def test_tired_guests_get_a_break_and_closing_runs_in_order():
    coach = Coach(RulesLLM(), storage.get_profile())
    r = coach.rules(guide_turn("This is the herb garden."), sit(tired=True, guest_turns_here=1))
    assert "break" in r.say
    closing = sit(stop_id="kitchen", stop_name="Courtyard lunch", stop_index=4, next_stop_name=None, guest_turns_here=1, last_kind="guide_statement")
    lines = [coach.rules(guide_turn(f"Line {i}."), closing).say for i in range(3)]
    assert "favourite part" in lines[0] and "review" in lines[1] and "see you again" in lines[2]


def test_coach_does_not_repeat_itself():
    coach = Coach(RulesLLM(), storage.get_profile())
    s = sit(guide_streak=2, last_kind="guide_statement")
    first = coach.rules(guide_turn("Look at these trees."), s).say
    second = coach.rules(guide_turn("They are very old."), s).say
    assert first != second


def test_product_already_priced_by_noor_is_not_pitched_again():
    coach = Coach(RulesLLM(), storage.get_profile())
    coach.observe(guide_turn("The 500 ml bottle is 8 JOD and the litre is 14 JOD."))
    s = sit(stop_id="press", stop_name="Stone olive press", questions_here=3, guest_turns_here=3, last_kind="guide_answer")
    r = coach.rules(guide_turn("Yes, it's cold pressed."), s)
    assert "8 JOD" not in r.say and "14 JOD" not in r.say


def test_blink_answers_stay_complete_with_a_yes_no_option():
    coach = Coach(RulesLLM(), storage.get_profile())
    t = guest_turn("I would like to come back again.", "kitchen")
    t.source = "blink"
    r = coach.rules(t, sit(stop_id="kitchen", stop_name="Courtyard lunch", stop_index=4, blink_guest=True))
    assert "WhatsApp" in r.say
    assert any("blink YES or NO" in o["say"] for o in r.options)


def test_question_saved_without_answer_is_never_used():
    # My farm lets the guide save a question now and answer it later.
    profile = storage.get_profile()
    profile["faq"] = [{"q": "How old are the trees?", "a": "", "keywords": ["old", "trees", "age"]}] + profile["faq"]
    assert "FAQ: How old are the trees? -> \n" not in format_briefing(profile) + "\n"
    coach = Coach(RulesLLM(), profile)
    # The empty entry scores at least as high, but the answered one must still win.
    result = coach.rules(guest_turn("How old are the trees?", "grove"), "grove")
    assert "300 years" in result.say
