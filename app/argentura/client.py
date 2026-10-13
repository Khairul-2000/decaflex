"""Argentura REST API Client.

Connects to the Argentura Management Suite (Node.js/Express backend),
handles session cookie persistence, CSRF tokens, and idempotent API mutations.
"""

import logging
import uuid
from typing import Any
import httpx

from app.argentura.models import (
    Appointment,
    DirectionalBuffers,
    ExtraService,
    LadyProfile,
    PriceStructure,
    TravelFeeConfig,
    WorkSchedule,
)

log = logging.getLogger("argentura.client")


class ArgenturaClient:
    def __init__(
        self,
        base_url: str = "https://www.management-suite.com",
        username: str = "",
        pin: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.pin = pin
        self.csrf_token: str | None = None
        self.agency_id: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def get_http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def login(self, username: str | None = None, pin: str | None = None) -> bool:
        """Authenticate with username and 6-digit numeric PIN."""
        user = username or self.username
        user_pin = pin or self.pin

        if not user or not user_pin:
            log.warning("Argentura credentials missing; skipping login")
            return False

        client = await self.get_http_client()
        try:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"username": user, "pin": user_pin},
            )
            if resp.status_code not in (200, 201):
                log.error("Argentura login failed: %s %s", resp.status_code, resp.text)
                return False

            # Retrieve active session details and CSRF token
            session_resp = await client.get("/api/v1/session")
            if session_resp.status_code == 200:
                raw_sess = session_resp.json()
                sess_data = raw_sess.get("data", raw_sess)
                self.csrf_token = sess_data.get("csrfToken") or sess_data.get("csrf")
                principal = sess_data.get("principal", {})
                self.agency_id = principal.get("agencyId") or sess_data.get("agencyId")
                log.info("Logged in to Argentura as agency %s, csrf: %s", self.agency_id, bool(self.csrf_token))
                return True
            return True
        except Exception:
            log.exception("Failed to connect to Argentura at %s", self.base_url)
            return False

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Idempotency-Key": str(uuid.uuid4()),
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/argentura/",
        }
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return headers

    # ---- Provider (Lady) Methods ----

    async def get_ladies(self) -> list[LadyProfile]:
        """Fetch list of all active ladies for the authenticated agency."""
        client = await self.get_http_client()
        resp = await client.get("/api/girls")
        if resp.status_code != 200:
            log.error("Failed to fetch ladies: %s %s", resp.status_code, resp.text)
            return []

        raw_list = resp.json()
        if isinstance(raw_list, dict) and "data" in raw_list:
            raw_list = raw_list["data"]

        profiles = []
        for item in raw_list:
            try:
                profiles.append(self._parse_lady_profile(item))
            except Exception:
                log.exception("Error parsing lady profile %s", item.get("id"))
        return profiles

    async def create_lady(self, lady_data: dict[str, Any]) -> dict[str, Any] | None:
        """Register a new lady profile under the agency."""
        client = await self.get_http_client()
        resp = await client.post(
            "/api/girls",
            json=lady_data,
            headers=self._auth_headers(),
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("data", data)
        log.error("Failed to create lady: %s %s", resp.status_code, resp.text)
        return None

    async def update_lady_profile(self, lady_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update a lady's profile fields via PUT /api/girls/{id}."""
        client = await self.get_http_client()
        resp = await client.put(
            f"/api/girls/{lady_id}",
            json=updates,
            headers=self._auth_headers(),
        )
        if resp.status_code in (200, 204):
            try:
                return resp.json()
            except Exception:
                return {"success": True}
        log.error("Failed to update lady %s: %s %s", lady_id, resp.status_code, resp.text)
        return None

    async def update_work_schedule(self, lady_id: str, schedule: dict[str, Any]) -> bool:
        """Update lady's working hours, breaks, and buffers via PUT /api/girls/{id}/work-schedule."""
        client = await self.get_http_client()
        resp = await client.put(
            f"/api/girls/{lady_id}/work-schedule",
            json=schedule,
            headers=self._auth_headers(),
        )
        return resp.status_code in (200, 204)

    async def get_lady_profile(self, lady_id: str) -> LadyProfile | None:
        """Fetch comprehensive profile for a specific lady including schedule and travel fee."""
        client = await self.get_http_client()
        resp = await client.get(f"/api/girls/{lady_id}")
        if resp.status_code != 200:
            log.error("Failed to fetch lady %s: %s", lady_id, resp.status_code)
            return None

        raw_profile = resp.json()
        profile = self._parse_lady_profile(raw_profile)

        # Also attempt to fetch travel fee configuration if not present
        if not profile.travelFee:
            tf_resp = await client.get(f"/api/v1/tenant/ladies/{lady_id}/travel-fee")
            if tf_resp.status_code == 200:
                tf_data = tf_resp.json()
                profile.travelFee = TravelFeeConfig(
                    maxDistanceKm=tf_data.get("maxDistanceKm", 25),
                    amountMinor=tf_data.get("amountMinor", 0),
                )

        return profile

    def _parse_lady_profile(self, data: dict[str, Any]) -> LadyProfile:
        prices = None
        if "structuredPrices" in data and isinstance(data["structuredPrices"], dict):
            prices = PriceStructure(**data["structuredPrices"])

        extras = []
        if "structuredExtras" in data and isinstance(data["structuredExtras"], list):
            for e in data["structuredExtras"]:
                if isinstance(e, dict):
                    extras.append(ExtraService(**e))

        travel_fee = None
        if "travelFee" in data and isinstance(data["travelFee"], dict):
            travel_fee = TravelFeeConfig(**data["travelFee"])

        schedule = None
        if "workSchedule" in data and isinstance(data["workSchedule"], dict):
            buffers_dict = data.get("transitionBufferMinutes")
            schedule = WorkSchedule.from_raw_dict(data["workSchedule"], buffers_dict=buffers_dict)

        addr = data.get("structuredAddress") if isinstance(data.get("structuredAddress"), dict) else {}
        street = addr.get("street") or data.get("street")
        house_number = addr.get("houseNumber") or data.get("houseNumber")
        zip_code = addr.get("zip") or data.get("zip")
        city = addr.get("city") or data.get("city")
        country = addr.get("country") or data.get("country", "Deutschland")
        doorbell = addr.get("doorbellName") or data.get("doorbellName")

        age_val = data.get("alter") or data.get("age") or 24

        return LadyProfile(
            id=str(data.get("id", "")),
            name=data.get("name", "Julia"),
            age=int(age_val),
            herkunft=data.get("herkunft"),
            sprache=data.get("sprache"),
            incall=bool(data.get("incall", True)),
            outcall=bool(data.get("outcall", False)),
            street=street,
            houseNumber=house_number,
            zip=zip_code,
            city=city,
            country=country,
            doorbellName=doorbell,
            timeZone=data.get("timeZone", "Europe/Berlin"),
            structuredPrices=prices,
            structuredExtras=extras,
            travelFee=travel_fee,
            aiInfo=data.get("aiInfo"),
            workSchedule=schedule,
            publicWorkPhone=data.get("publicWorkPhone") or data.get("whatsappConnectedPhone"),
        )

    # ---- Calendar & Appointment Methods ----

    async def get_appointments(
        self,
        lady_id: str | None = None,
        date: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[Appointment]:
        """Query appointments for a lady and optional date range."""
        client = await self.get_http_client()
        params: dict[str, Any] = {"projection": "active-calendar"}
        if date:
            params["from"] = date
            params["to"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        url = f"/api/girls/{lady_id}/termine" if lady_id else "/api/termine"
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            log.error("Failed to query appointments from %s: %s", url, resp.status_code)
            return []

        raw_list = resp.json()
        if isinstance(raw_list, dict) and "termine" in raw_list:
            raw_list = raw_list["termine"]
        elif isinstance(raw_list, dict) and "data" in raw_list:
            raw_list = raw_list["data"]

        appointments = []
        if isinstance(raw_list, list):
            for item in raw_list:
                try:
                    appointments.append(
                        Appointment(
                            id=str(item.get("id", "")),
                            agencyId=item.get("agencyId"),
                            ladyId=str(item.get("ladyId", lady_id or "")),
                            locationType=item.get("locationType", "incall"),
                            customerAddress=item.get("customerAddress") or item.get("locationAddressText"),
                            date=item.get("date", ""),
                            startTime=item.get("startTime", ""),
                            endTime=item.get("endTime", ""),
                            customerName=item.get("customerName", "Guest"),
                            customerPhone=item.get("customerPhone", ""),
                            serviceType=item.get("serviceType", ""),
                            price=float(item.get("price", 0.0) or 0.0),
                            notes=item.get("notes"),
                            status=item.get("status", "angefragt"),
                            version=int(item.get("version", 1)),
                        )
                    )
                except Exception:
                    log.exception("Error parsing appointment")
        return appointments

    async def create_appointment(self, appointment: Appointment) -> Appointment | None:
        """Create a new booking with initial status 'angefragt'."""
        client = await self.get_http_client()
        
        # Calculate duration in minutes if not present
        start_m = int(appointment.startTime.split(":")[0]) * 60 + int(appointment.startTime.split(":")[1])
        end_m = int(appointment.endTime.split(":")[0]) * 60 + int(appointment.endTime.split(":")[1])
        duration = max(15, end_m - start_m)

        payload = {
            "customerName": appointment.customerName,
            "customerPhone": appointment.customerPhone,
            "date": appointment.date,
            "startTime": appointment.startTime,
            "endTime": appointment.endTime,
            "duration": duration,
            "notes": appointment.notes or "",
            "serviceType": appointment.serviceType or f"{duration} min",
            "locationType": appointment.locationType or "incall",
            "locationAddressText": appointment.customerAddress or "",
            "status": appointment.status or "angefragt",
            "price": appointment.price,
            "ladyId": appointment.ladyId,
        }

        url = f"/api/girls/{appointment.ladyId}/termine"
        resp = await client.post(
            url,
            json=payload,
            headers=self._auth_headers(),
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            if isinstance(data, dict):
                appointment.id = str(data.get("id") or data.get("terminId") or "")
            log.info("Created appointment %s for lady %s", appointment.id, appointment.ladyId)
            return appointment
        log.error("Failed to create appointment via %s: %s %s", url, resp.status_code, resp.text)
        return None

    async def update_appointment(self, appt_id: str, updates: dict[str, Any]) -> bool:
        """Update appointment (e.g. rescheduling or status transition)."""
        client = await self.get_http_client()
        resp = await client.patch(
            f"/api/termine/{appt_id}",
            json=updates,
            headers=self._auth_headers(),
        )
        return resp.status_code == 200

    # ---- Chat & Messaging Methods ----

    async def send_chat_message(self, chat_id: str, text: str) -> bool:
        """Send an outbound customer message via Argentura's Baileys WhatsApp channel."""
        client = await self.get_http_client()
        resp = await client.post(
            f"/api/chats/{chat_id}/messages",
            json={"text": text},
            headers=self._auth_headers(),
        )
        return resp.status_code in (200, 201)

    async def get_chat_mode(self, chat_id: str) -> str:
        """Returns 'ai' or 'human'."""
        client = await self.get_http_client()
        resp = await client.get(f"/api/chats/{chat_id}")
        if resp.status_code == 200:
            return resp.json().get("mode", "ai")
        return "ai"
