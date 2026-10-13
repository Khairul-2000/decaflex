"""LangChain Tools for Argentura Appointment Booking and Availability."""

from datetime import datetime
import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.argentura.client import ArgenturaClient
from app.argentura.models import Appointment, LadyProfile
from app.services.availability import AvailabilityEngine, minutes_to_time, time_to_minutes
from app.services.geolocation import GeolocationService

log = logging.getLogger("argentura.tools")


# --- Tool Argument Schemas ---

class CheckAvailabilitySchema(BaseModel):
    date: str = Field(description="Date in YYYY-MM-DD format, e.g. 2026-10-15")
    time: str = Field(description="Desired start time in HH:MM format (24-hour), e.g. 18:00")
    duration_minutes: int = Field(default=60, description="Duration in minutes (e.g. 30, 45, 60, 120)")
    location_type: str = Field(default="incall", description="'incall' (at studio) or 'outcall' (at client location)")


class CheckDistanceSchema(BaseModel):
    customer_address: str = Field(description="Street address, hotel name, or postal code of the customer")


class CalculatePriceSchema(BaseModel):
    duration_minutes: int = Field(description="Duration in minutes (e.g. 60, 120)")
    extras: list[str] = Field(default_factory=list, description="List of extra service names requested")
    location_type: str = Field(default="incall", description="'incall' or 'outcall'")


class BookAppointmentSchema(BaseModel):
    customer_name: str = Field(description="Name or nickname of the customer")
    customer_phone: str = Field(description="Phone number of the customer")
    date: str = Field(description="Date in YYYY-MM-DD format")
    time: str = Field(description="Start time in HH:MM format")
    duration_minutes: int = Field(default=60, description="Duration in minutes")
    location_type: str = Field(default="incall", description="'incall' or 'outcall'")
    customer_address: str | None = Field(default=None, description="Customer address if outcall")
    notes: str | None = Field(default=None, description="Special requests or notes")


class BookingToolkit:
    """Toolkit bound to a specific lady profile and backend services."""

    def __init__(
        self,
        lady: LadyProfile,
        client: ArgenturaClient,
        geo_service: GeolocationService | None = None,
    ):
        self.lady = lady
        self.client = client
        self.geo_service = geo_service or GeolocationService()

    def get_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                coroutine=self.check_slot_availability,
                name="check_slot_availability",
                description=(
                    "Check if a requested appointment slot is free in the lady's calendar, "
                    "taking into account working hours, breaks, and transition buffer times. "
                    "Returns availability status and alternative times if busy."
                ),
                args_schema=CheckAvailabilitySchema,
            ),
            StructuredTool.from_function(
                coroutine=self.check_outcall_distance,
                name="check_outcall_distance",
                description=(
                    "Validate customer address for outcall visits. Checks if the destination "
                    "is within the allowed maximum driving distance and calculates travel surcharge."
                ),
                args_schema=CheckDistanceSchema,
            ),
            StructuredTool.from_function(
                func=self.calculate_price_quote,
                name="calculate_price_quote",
                description="Calculate total price quote in EUR based on duration and optional extras.",
                args_schema=CalculatePriceSchema,
            ),
            StructuredTool.from_function(
                coroutine=self.book_appointment,
                name="book_appointment",
                description=(
                    "Lock in and reserve an appointment in the Argentura calendar with status 'angefragt'. "
                    "Call this only after the customer has agreed on date, time, duration, and price."
                ),
                args_schema=BookAppointmentSchema,
            ),
        ]

    async def check_slot_availability(
        self,
        date: str,
        time: str,
        duration_minutes: int = 60,
        location_type: str = "incall",
    ) -> str:
        appointments = await self.client.get_appointments(lady_id=self.lady.id, date=date)
        is_free, reason, alternatives = AvailabilityEngine.is_slot_available(
            self.lady,
            appointments,
            date,
            time,
            duration_minutes,
            location_type,
        )

        if is_free:
            return json.dumps({
                "status": "available",
                "message": f"Slot on {date} at {time} ({duration_minutes} min, {location_type}) is open and available.",
            })
        else:
            return json.dumps({
                "status": "unavailable",
                "reason": reason,
                "suggested_alternatives": alternatives,
                "message": f"Slot is unavailable ({reason}). Available alternative start times on {date}: {', '.join(alternatives) if alternatives else 'none on this day'}.",
            })

    async def check_outcall_distance(self, customer_address: str) -> str:
        if not self.lady.outcall:
            return json.dumps({
                "allowed": False,
                "reason": f"{self.lady.name} currently only offers In-Call appointments at her studio.",
            })

        max_dist = self.lady.travelFee.maxDistanceKm if self.lady.travelFee else 25
        fee_minor = self.lady.travelFee.amountMinor if self.lady.travelFee else 0

        ok, dist_km, fee_eur, msg = await self.geo_service.validate_outcall(
            customer_address,
            self.lady.studio_address,
            max_dist,
            fee_minor,
        )

        return json.dumps({
            "allowed": ok,
            "distance_km": dist_km,
            "travel_fee_eur": fee_eur,
            "message": msg,
        })

    def calculate_price_quote(
        self,
        duration_minutes: int,
        extras: list[str] = [],
        location_type: str = "incall",
    ) -> str:
        prices = self.lady.structuredPrices
        base_price = 0
        if prices:
            tier = prices.get_tier_price(duration_minutes)
            if tier:
                base_price = tier
            else:
                # Pro-rate from 60 min if non-standard
                rate_60 = prices.p60 or 150
                base_price = int((rate_60 / 60) * duration_minutes)
        else:
            base_price = 150

        extras_total = 0.0
        applied_extras = []
        for requested in extras:
            for ex in self.lady.structuredExtras:
                if ex.name.lower() in requested.lower() or requested.lower() in ex.name.lower():
                    extras_total += ex.price_eur
                    applied_extras.append(f"{ex.name} (+€{ex.price_eur:.2f})")

        travel_fee = 0.0
        if location_type.lower() == "outcall" and self.lady.travelFee:
            travel_fee = self.lady.travelFee.fee_eur

        total = base_price + extras_total + travel_fee
        return json.dumps({
            "base_price_eur": base_price,
            "duration_minutes": duration_minutes,
            "extras": applied_extras,
            "extras_total_eur": extras_total,
            "travel_fee_eur": travel_fee,
            "total_price_eur": total,
            "formatted": f"Total: €{total:.2f} (Base {duration_minutes}m: €{base_price}"
            + (f", Extras: +€{extras_total:.2f}" if extras_total else "")
            + (f", Travel fee: +€{travel_fee:.2f}" if travel_fee else "")
            + ")",
        })

    async def book_appointment(
        self,
        customer_name: str,
        customer_phone: str,
        date: str,
        time: str,
        duration_minutes: int = 60,
        location_type: str = "incall",
        customer_address: str | None = None,
        notes: str | None = None,
    ) -> str:
        start_mins = time_to_minutes(time)
        end_time = minutes_to_time(start_mins + duration_minutes)

        quote_data = json.loads(self.calculate_price_quote(duration_minutes, location_type=location_type))
        total_price = float(quote_data["total_price_eur"])

        service_desc = f"{duration_minutes} min {location_type.upper()}"

        appt = Appointment(
            ladyId=self.lady.id,
            locationType=location_type,
            customerAddress=customer_address,
            date=date,
            startTime=time,
            endTime=end_time,
            customerName=customer_name,
            customerPhone=customer_phone,
            serviceType=service_desc,
            price=total_price,
            notes=notes,
            status="angefragt",
        )

        created = await self.client.create_appointment(appt)
        if created and created.id:
            return json.dumps({
                "success": True,
                "appointment_id": created.id,
                "date": date,
                "start_time": time,
                "end_time": end_time,
                "price_eur": total_price,
                "status": "angefragt",
                "message": (
                    f"Appointment successfully reserved for {date} from {time} to {end_time}. "
                    f"Total price is €{total_price:.2f}. Status: Requested (angefragt)."
                ),
            })
        else:
            return json.dumps({
                "success": False,
                "message": "Failed to create appointment in Argentura calendar. Please try again or contact operator.",
            })
