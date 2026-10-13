"""Tests for Booking Toolkit and Price Quotes."""

import json
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
