"""Tests for Anti-Disclosure Stealth Guard and Dynamic Persona Builder."""

from app.argentura.models import ExtraService, LadyProfile, PriceStructure, TravelFeeConfig
from app.guards import detect_ai_disclosure
from app.persona_builder import build_lady_system_prompt


def test_ai_disclosure_detection():
    # Accidental leaks by model
    assert detect_ai_disclosure("I am an AI assistant and I don't have a body.")
    assert detect_ai_disclosure("As an artificial intelligence, I cannot meet you.")
    assert detect_ai_disclosure("I'm a bot designed to help you book appointments.")
    assert detect_ai_disclosure("I cannot meet in person because I'm an AI.")
    assert detect_ai_disclosure("I'm not a real person.")

    # Legitimate in-character text
    assert not detect_ai_disclosure("Hey Peter! I'd love to see you tomorrow at 6 PM 😏")
    assert not detect_ai_disclosure("Haha a bot? Of course I'm real silly, just checking my schedule!")
    assert not detect_ai_disclosure("My studio is on Mainzer Str. 12.")


def test_dynamic_persona_builder():
    prices = PriceStructure(p30=80, p60=150, p120=280)
    extras = [ExtraService(extraId="e1", name="Champagne", amountMinor=4000)]
    travel = TravelFeeConfig(maxDistanceKm=30, amountMinor=2500)

    lady = LadyProfile(
        id="lady-julia",
        name="Julia",
        age=23,
        street="Kaiserstr.",
        houseNumber="45",
        city="Frankfurt",
        structuredPrices=prices,
        structuredExtras=extras,
        travelFee=travel,
        incall=True,
        outcall=True,
        aiInfo="Direct, playful, sensual.",
    )

    prompt = build_lady_system_prompt(lady)

    assert "Julia" in prompt
    assert "23" in prompt
    assert "UNDER NO CIRCUMSTANCES DO YOU EVER MENTION" in prompt
    assert "1 hour (60 min): €150" in prompt
    assert "Champagne: +€40.00" in prompt
    assert "30 km radius" in prompt
    assert "€25.00" in prompt
