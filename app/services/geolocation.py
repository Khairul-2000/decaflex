"""Outcall Geolocation and Distance Validation Service."""

import logging
import math
import httpx

log = logging.getLogger("argentura.geolocation")

# Simple in-memory geocoding cache: {address_str: (lat, lon)}
_GEO_CACHE: dict[str, tuple[float, float]] = {}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two coordinates in kilometers."""
    r = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


class GeolocationService:
    def __init__(self, osrm_base_url: str = "https://router.project-osrm.org"):
        self.osrm_base_url = osrm_base_url.rstrip("/")

    async def geocode(self, address: str) -> tuple[float, float] | None:
        """Geocode an address to (latitude, longitude) using Nominatim OpenStreetMap."""
        clean_addr = address.strip()
        if clean_addr in _GEO_CACHE:
            return _GEO_CACHE[clean_addr]

        url = "https://nominatim.openstreetmap.org/search"
        headers = {"User-Agent": "ArgenturaBookingBot/1.0 (contact@management-suite.com)"}
        params = {"q": clean_addr, "format": "json", "limit": 1}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    results = resp.json()
                    if results and len(results) > 0:
                        lat = float(results[0]["lat"])
                        lon = float(results[0]["lon"])
                        _GEO_CACHE[clean_addr] = (lat, lon)
                        return (lat, lon)
        except Exception:
            log.exception("Geocoding failed for %s", address)
        return None

    async def calculate_distance_km(
        self, origin_address: str, destination_address: str
    ) -> float | None:
        """Calculate driving/route distance in kilometers between two addresses."""
        orig = await self.geocode(origin_address)
        dest = await self.geocode(destination_address)

        if not orig or not dest:
            return None

        # Attempt OSRM driving route first
        try:
            osrm_url = (
                f"{self.osrm_base_url}/route/v1/driving/"
                f"{orig[1]},{orig[0]};{dest[1]},{dest[0]}?overview=false"
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(osrm_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and "routes" in data and len(data["routes"]) > 0:
                        distance_meters = data["routes"][0]["distance"]
                        return distance_meters / 1000.0
        except Exception:
            log.debug("OSRM route calculation unavailable; falling back to Haversine distance")

        # Fallback: Straight-line Haversine distance multiplied by 1.3 (typical road tortuosity factor)
        return haversine_km(orig[0], orig[1], dest[0], dest[1]) * 1.3

    async def validate_outcall(
        self,
        customer_address: str,
        lady_studio_address: str,
        max_distance_km: int,
        travel_fee_minor: int,
    ) -> tuple[bool, float | None, float, str]:
        """Validate if outcall destination is within permitted radius.

        Returns: (is_within_range, distance_km, surcharge_eur, message)
        """
        fee_eur = travel_fee_minor / 100.0
        dist = await self.calculate_distance_km(lady_studio_address, customer_address)

        if dist is None:
            # Address could not be pinpointed with certainty
            return (
                False,
                None,
                fee_eur,
                "Could not verify address location. Please provide city and postal code.",
            )

        if dist <= max_distance_km:
            return (
                True,
                round(dist, 1),
                fee_eur,
                f"Within service radius ({round(dist, 1)} km <= {max_distance_km} km max). Travel fee: €{fee_eur:.2f}",
            )
        else:
            return (
                False,
                round(dist, 1),
                fee_eur,
                f"Location is {round(dist, 1)} km away, which exceeds maximum travel distance of {max_distance_km} km.",
            )
