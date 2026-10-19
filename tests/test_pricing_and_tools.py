"""Tests for Booking Toolkit and Price Quotes."""

import json
import pytest

from app.argentura.client import ArgenturaClient
from app.argentura.models import ExtraService, LadyProfile, PriceStructure, TravelFeeConfig
from app.tools.booking_tools import BookingToolkit


def test_calculate_price_quote():
    prices = PriceStructure(p30=80, p60=150, p120=280)
    extras = [
        ExtraService(extraId="e1", name="Massage", amountMinor=3000),
        ExtraService(extraId="e2", name="Dinner Date", amountMinor=5000),
    ]
    travel = TravelFeeConfig(maxDistanceKm=20, amountMinor=2000)

    lady = LadyProfile(
        id="lady-1",
        name="Julia",
        structuredPrices=prices,
        structuredExtras=extras,
        travelFee=travel,
        incall=True,
        outcall=True,
    )

    client = ArgenturaClient()
    toolkit = BookingToolkit(lady=lady, client=client)

    # 1 hour Incall without extras: €150
    quote1_raw = toolkit.calculate_price_quote(duration_minutes=60, location_type="incall")
    quote1 = json.loads(quote1_raw)
    assert quote1["base_price_eur"] == 150
    assert quote1["total_price_eur"] == 150

    # 1 hour Outcall + Massage (+30 EUR) + Travel (+20 EUR): 150 + 30 + 20 = 200 EUR
    quote2_raw = toolkit.calculate_price_quote(
        duration_minutes=60,
        extras=["Massage"],
        location_type="outcall",
    )
    quote2 = json.loads(quote2_raw)
    assert quote2["base_price_eur"] == 150
    assert quote2["extras_total_eur"] == 30.0
    assert quote2["travel_fee_eur"] == 20.0
    assert quote2["total_price_eur"] == 200.0


def test_book_appointment_fails_on_empty_slots_or_no_context():
    import asyncio

    lady = LadyProfile(
        id="greenfield-lady:diana_lila",
        name="Lila",
        structuredPrices=PriceStructure(p60=60),
        incall=True,
    )

    # 1. Without availability_context and failing REST client -> returns failure
    toolkit_no_ctx = BookingToolkit(
        lady=lady,
        client=ArgenturaClient(),
        availability_context=None,
        sender_name="Alex",
        sender_phone="+4917622114327",
    )
    res_no_ctx = json.loads(asyncio.run(toolkit_no_ctx.book_appointment(
        date="2026-10-17", time="15:00", duration_minutes=60, location_type="incall"
    )))
    assert res_no_ctx["success"] is False
    assert toolkit_no_ctx.last_booking_intent is None

    # 2. With availability_context containing empty slots list -> returns failure (Codex defect 1 reproduction)
    toolkit_empty_slots = BookingToolkit(
        lady=lady,
        client=ArgenturaClient(),
        availability_context={"timeZone": "Europe/Berlin", "slots": []},
        sender_name="Alex",
        sender_phone="+4917622114327",
    )
    res_empty = json.loads(asyncio.run(toolkit_empty_slots.book_appointment(
        date="2026-10-17", time="15:00", duration_minutes=60, location_type="incall"
    )))
    assert res_empty["success"] is False
    assert res_empty["status"] == "unavailable"
    assert toolkit_empty_slots.last_booking_intent is None


def test_book_appointment_booking_intent_greenfield():
    import asyncio

    lady = LadyProfile(
        id="greenfield-lady:diana_lila",
        name="Lila",
        structuredPrices=PriceStructure(p60=60),
        incall=True,
    )
    # Valid slot covering 2026-10-17 15:00-16:00 (13:00-17:00 UTC)
    toolkit = BookingToolkit(
        lady=lady,
        client=ArgenturaClient(),
        availability_context={
            "timeZone": "Europe/Berlin",
            "slots": [
                {
                    "startsAt": "2026-10-17T13:00:00.000Z",
                    "endsAt": "2026-10-17T17:00:00.000Z",
                    "status": "available",
                }
            ],
        },
        sender_name="Alex",
        sender_phone="+4917622114327",
    )

    # Call book_appointment without customer_name or customer_phone
    result_raw = asyncio.run(toolkit.book_appointment(
        date="2026-10-17",
        time="15:00",
        duration_minutes=60,
        location_type="incall",
    ))
    result = json.loads(result_raw)

    assert result["success"] is True
    assert result["appointment_id"] is None  # No synthetic draft-* ID!
    assert result["status"] == "angefragt"
    assert result["customer_name"] == "Alex"
    assert result["customer_phone"] == "+4917622114327"
    assert toolkit.last_booking_intent is not None
    assert toolkit.last_booking_intent["lady_id"] == "greenfield-lady:diana_lila"
    assert toolkit.last_booking_intent["date"] == "2026-10-17"
    assert toolkit.last_booking_intent["start_time"] == "15:00"
    assert toolkit.last_booking_intent["end_time"] == "16:00"
    assert toolkit.last_booking_intent["price_eur"] == 60.0
    assert toolkit.last_booking_intent["customer_name"] == "Alex"
    assert toolkit.last_booking_intent["customer_phone"] == "+4917622114327"

