"""Elering NPS (Nord Pool spot) price client.

Wraps the public dashboard endpoint at
``https://dashboard.elering.ee/api/nps/price`` to fetch hourly EE-zone
spot prices. Maintains a process-lifetime in-memory cache keyed by
top-of-hour UTC so a single coordinator tick that walks overlapping
windows hits the network at most once per gap.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import aiohttp

NPS_PRICE_URL = "https://dashboard.elering.ee/api/nps/price"
NPS_TIMEOUT_SECONDS = 30

_LOGGER = logging.getLogger(__name__)


class NpsError(Exception):
    """Raised when the NPS endpoint returns a non-200 status or unparseable body."""


class EleringNpsClient:
    """Async client for Elering's public NPS price endpoint.

    No auth required. Returns prices in EUR/kWh (converted from the
    endpoint's native EUR/MWh).
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        # Top-of-hour UTC → EUR/kWh
        self._cache: dict[datetime, float] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    async def async_get_prices(self, start: datetime, end: datetime) -> dict[datetime, float]:
        """Return hourly EE prices in [start, end) keyed by top-of-hour UTC.

        Cache-first: every hour already present in the cache is returned
        without an HTTP call. The gap (if any) is fetched in a single
        request covering the missing range. Empty ranges return ``{}``
        without hitting the network.
        """
        start = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        end = end.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if end <= start:
            return {}

        wanted: list[datetime] = []
        cursor = start
        while cursor < end:
            wanted.append(cursor)
            cursor += timedelta(hours=1)

        missing = [h for h in wanted if h not in self._cache]
        if missing:
            await self._fetch_range(missing[0], missing[-1] + timedelta(hours=1))

        return {h: self._cache[h] for h in wanted if h in self._cache}

    async def _fetch_range(self, start: datetime, end: datetime) -> None:
        params = {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        }
        try:
            async with self._session.get(
                NPS_PRICE_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=NPS_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    raise NpsError(f"NPS returned status {resp.status}")
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise NpsError(f"NPS request failed: {err}") from err
        except TimeoutError as err:
            raise NpsError(f"NPS request timed out: {err}") from err

        rows = (payload or {}).get("data", {}).get("ee", [])
        for row in rows:
            ts = row.get("timestamp")
            price_eur_per_mwh = row.get("price")
            if ts is None or price_eur_per_mwh is None:
                continue
            hour = datetime.fromtimestamp(int(ts), tz=UTC).replace(
                minute=0, second=0, microsecond=0
            )
            self._cache[hour] = float(price_eur_per_mwh) / 1000.0
