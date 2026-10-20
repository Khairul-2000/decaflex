"""Data models for Argentura platform entities."""

from typing import Any
from pydantic import BaseModel, Field


class PriceStructure(BaseModel):
    """Structured duration pricing tiers."""
    p20: int | None = None
    p30: int | None = None
    p45: int | None = None
    p60: int | None = None
    p120: int | None = None
    p180: int | None = None
    p240: int | None = None
    overnight: int | None = None

    def get_tier_price(self, duration_minutes: int) -> int | None:
        """Get the price (EUR) for the closest standard duration tier."""
        tier_map = {
            20: self.p20,
            30: self.p30,
            45: self.p45,
            60: self.p60,
            120: self.p120,
            180: self.p180,
            240: self.p240,
        }
        return tier_map.get(duration_minutes)


class ExtraService(BaseModel):
    """Custom extra add-ons configured per lady."""
    extraId: str
    name: str
    amountMinor: int = 0  # In cents (e.g. 5000 = 50.00 EUR)
    amountMajor: str | None = None
    currencyCode: str = "EUR"
    description: str | None = None
    active: bool = True
    sortOrder: int = 0

    @property
    def price_eur(self) -> float:
        return self.amountMinor / 100.0


class DirectionalBuffers(BaseModel):
    """Directional transition buffer rules (minutes)."""
    incallToIncall: int = 30
    incallToOutcall: int = 60
    outcallToOutcall: int = 60
    outcallToIncall: int = 60


class DaySchedule(BaseModel):
    on: bool = False
    start: str = "10:00"
    end: str = "22:00"
    breaks: list[dict[str, str]] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict | None) -> "DaySchedule":
        if not raw or not isinstance(raw, dict):
            return cls(on=False)
        return cls(
            on=bool(raw.get("on", True)),
            start=raw.get("start", "10:00"),
            end=raw.get("end", "20:00"),
            breaks=raw.get("breaks", []),
        )


class WorkSchedule(BaseModel):
    mo: DaySchedule = Field(default_factory=DaySchedule)
    tu: DaySchedule = Field(default_factory=DaySchedule)
    we: DaySchedule = Field(default_factory=DaySchedule)
    th: DaySchedule = Field(default_factory=DaySchedule)
    fr: DaySchedule = Field(default_factory=DaySchedule)
    sa: DaySchedule = Field(default_factory=DaySchedule)
    su: DaySchedule = Field(default_factory=DaySchedule)
    scheduleTransitionBuffers: DirectionalBuffers = Field(default_factory=DirectionalBuffers)

    @classmethod
    def from_raw_dict(cls, data: dict, buffers_dict: dict | None = None) -> "WorkSchedule":
        """Parse from raw Argentura workSchedule dictionary with German (di, mi, do, so) or English keys."""
        mo = DaySchedule.from_raw(data.get("mo"))
        tu = DaySchedule.from_raw(data.get("di") or data.get("tu"))
        we = DaySchedule.from_raw(data.get("mi") or data.get("we"))
        th = DaySchedule.from_raw(data.get("do") or data.get("th"))
        fr = DaySchedule.from_raw(data.get("fr"))
        sa = DaySchedule.from_raw(data.get("sa"))
        su = DaySchedule.from_raw(data.get("so") or data.get("su"))

        buf_raw = buffers_dict or data.get("transitionBufferMinutes") or data.get("scheduleTransitionBuffers") or {}
        buffers = DirectionalBuffers(**buf_raw) if isinstance(buf_raw, dict) else DirectionalBuffers()

        return cls(
            mo=mo,
            tu=tu,
            we=we,
            th=th,
            fr=fr,
            sa=sa,
            su=su,
            scheduleTransitionBuffers=buffers,
        )

    def get_day(self, weekday_iso: int) -> DaySchedule:
        """1 = Monday, 7 = Sunday."""
        mapping = {
            1: self.mo,
            2: self.tu,
            3: self.we,
            4: self.th,
            5: self.fr,
            6: self.sa,
            7: self.su,
        }
        return mapping.get(weekday_iso, DaySchedule())


class TravelFeeConfig(BaseModel):
    maxDistanceKm: int = 25
    amountMinor: int = 0

    @property
    def fee_eur(self) -> float:
        return self.amountMinor / 100.0


class LadyProfile(BaseModel):
    id: str
    name: str
    age: int = 24
    herkunft: str | None = None
    sprache: str | None = None
    incall: bool = True
    outcall: bool = False
    street: str | None = None
    houseNumber: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = "Deutschland"
    doorbellName: str | None = None
    timeZone: str = "Europe/Berlin"
    structuredPrices: PriceStructure | None = None
    structuredExtras: list[ExtraService] = Field(default_factory=list)
    travelFee: TravelFeeConfig | None = None
    aiInfo: str | None = None
    workSchedule: WorkSchedule | None = None
    publicWorkPhone: str | None = None

    @property
    def studio_address(self) -> str:
        street_part = f"{self.street or ''} {self.houseNumber or ''}".strip()
        parts = [street_part, self.zip, self.city]
        valid = [p for p in parts if p]
        return ", ".join(valid) if valid else (street_part or "meiner Adresse")

    @classmethod
    def from_lady_context(
        cls,
        lady_id: str,
        lady_context: dict[str, Any] | None = None,
        fallback_name: str = "Lila",
    ) -> "LadyProfile":
        """Build a LadyProfile dynamically from Argentura's real ladyContext."""
        if not lady_context or not isinstance(lady_context, dict):
            return cls(id=lady_id, name=fallback_name)

        name = lady_context.get("displayName") or fallback_name
        personality = lady_context.get("personality") or ""
        important_info = lady_context.get("importantInformation") or ""
        ai_info = f"{personality}\n{important_info}".strip() or None

        prices_dict: dict[str, int] = {}
        services = lady_context.get("services") or []
        for s in services:
            if not isinstance(s, dict):
                continue
            dur = s.get("durationMinutes")
            minor = s.get("amountMinor")
            if dur and minor is not None:
                try:
                    major = int(minor) // 100
                except (ValueError, TypeError):
                    major = 0
                if dur == 20: prices_dict["p20"] = major
                elif dur == 30: prices_dict["p30"] = major
                elif dur == 45: prices_dict["p45"] = major
                elif dur == 60: prices_dict["p60"] = major
                elif dur == 120: prices_dict["p120"] = major
                elif dur == 180: prices_dict["p180"] = major
                elif dur == 240: prices_dict["p240"] = major

        structured_prices = PriceStructure(**prices_dict) if prices_dict else None

        extras_list: list[ExtraService] = []
        raw_extras = lady_context.get("extras") or []
        for e in raw_extras:
            if not isinstance(e, dict):
                continue
            extra_id = str(e.get("extraId") or e.get("id") or f"ext_{len(extras_list)}")
            extras_list.append(ExtraService(
                extraId=extra_id,
                name=e.get("name", "Extra"),
                amountMinor=int(e.get("amountMinor") or 0),
                amountMajor=e.get("displayAmount"),
                description=e.get("description"),
            ))

        incall_addr = lady_context.get("incallAddress") or {}
        street = incall_addr.get("street") if isinstance(incall_addr, dict) else None
        house_num = incall_addr.get("houseNumber") if isinstance(incall_addr, dict) else None
        postal_code = incall_addr.get("zip") if isinstance(incall_addr, dict) else None
        city = incall_addr.get("city") if isinstance(incall_addr, dict) else None

        allowed_visit_types = lady_context.get("allowedVisitTypes")
        if isinstance(allowed_visit_types, list):
            incall = "incall" in allowed_visit_types
            outcall = "outcall" in allowed_visit_types
        else:
            incall = bool(lady_context.get("incall", True))
            outcall = bool(lady_context.get("outcall", False))

        travel_fee = None
        travel_fee_raw = lady_context.get("travelFeeRule") or lady_context.get("travelFee")
        if isinstance(travel_fee_raw, dict):
            try:
                travel_fee = TravelFeeConfig(
                    maxDistanceKm=int(travel_fee_raw.get("maxDistanceKm", 25)),
                    amountMinor=int(travel_fee_raw.get("amountMinor", 0)),
                )
            except Exception:
                pass

        return cls(
            id=lady_id,
            name=name,
            sprache=lady_context.get("language") or "de",
            incall=incall,
            outcall=outcall,
            street=street,
            houseNumber=house_num,
            zip=postal_code,
            city=city,
            structuredPrices=structured_prices,
            structuredExtras=extras_list,
            travelFee=travel_fee,
            aiInfo=ai_info,
        )



class Appointment(BaseModel):
    id: str | None = None
    agencyId: str | None = None
    ladyId: str
    locationType: str = "incall"  # "incall" | "outcall"
    customerAddress: str | None = None
    date: str  # YYYY-MM-DD
    startTime: str  # HH:MM
    endTime: str  # HH:MM
    customerName: str
    customerPhone: str
    serviceType: str
    price: float
    notes: str | None = None
    status: str = "angefragt"  # angefragt, bestaetigt, storniert, etc.
    version: int = 1
