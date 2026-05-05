"""Verify ASSESSMENT_DATA contains all 48 personality_profiler questions
and self_efficacy_scale's 10 items."""

from __future__ import annotations


def test_personality_profiler_has_48_questions():
    from routers.chat_wrapper_assessments import ASSESSMENT_DATA

    pp = ASSESSMENT_DATA["personality_profiler"]
    assert len(pp["questions"]) == 48
    assert pp["questions"][1] == "Does your mood fluctuate?"
    assert pp["questions"][48] == "Can you initiate and bring life to a party?"


def test_self_efficacy_scale_has_10_items():
    from routers.chat_wrapper_assessments import ASSESSMENT_DATA

    ses = ASSESSMENT_DATA["self_efficacy_scale"]
    assert len(ses["questions"]) == 10
    assert ses["questions"][0] == "I can solve tedious problems with sincere efforts."
    assert ses["questions"][-1] == "I am mostly capable of handling anything that crosses my path."


def test_personality_profiler_has_scoring_and_interpretations():
    from routers.chat_wrapper_assessments import ASSESSMENT_DATA

    pp = ASSESSMENT_DATA["personality_profiler"]
    assert set(pp["scoring"].keys()) == {
        "Non-Conformist", "Sociable", "Emotionally Unstable", "Socially Desirable",
    }
    assert "Sociable" in pp["interpretations"]


def test_generate_wellness_report_includes_physical_health_metrics(monkeypatch):
    """The chat report response must contain physical_health_metrics
    (default empty dict if no signals); frontend expects this key."""
    from unittest.mock import MagicMock
    import asyncio

    from routers import chat_wrapper

    fake = MagicMock()
    fake.model_dump.return_value = {
        "mood_rating": 6,
        "stress_level": 4,
        "anxiety_level": 3,
        "energy_level": 7,
        "overall_wellness": 7,
        "risk_level": "low",
    }
    monkeypatch.setattr(chat_wrapper, "run_report", lambda **kw: fake)

    session = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()

    out = asyncio.run(chat_wrapper.generate_wellness_report(
        messages=[{"sender": "user", "content": "I'm tired"}],
        session_type="text",
        session_duration=15,
        user_id="u-1",
        company_id_str="",
        db=session,
    ))

    assert out["type"] == "report"
    assert "physical_health_metrics" in out["data"]
    assert isinstance(out["data"]["physical_health_metrics"], dict)
