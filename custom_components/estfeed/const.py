"""Constants for the Estfeed integration."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Final

DOMAIN: Final = "estfeed"

CONF_CLIENT_ID: Final = "client_id"
CONF_CLIENT_SECRET: Final = "client_secret"
CONF_FRIENDLY_NAME: Final = "friendly_name"
CONF_RESOLUTION: Final = "resolution"
CONF_BACKFILL_MONTHS: Final = "backfill_months"

DEFAULT_FRIENDLY_NAME: Final = "Estfeed"
DEFAULT_BACKFILL_MONTHS: Final = 12
MAX_BACKFILL_MONTHS: Final = 84
MIN_BACKFILL_MONTHS: Final = 1

CONF_VAT_PERCENT: Final = "vat_percent"
CONF_MARGIN_EUR_PER_KWH: Final = "margin_eur_per_kwh"
CONF_FEES_EUR_PER_KWH: Final = "fees_eur_per_kwh"
CONF_GRID_DAY_EUR_PER_KWH: Final = "grid_day_eur_per_kwh"
CONF_GRID_NIGHT_EUR_PER_KWH: Final = "grid_night_eur_per_kwh"
CONF_NIGHT_START_HOUR: Final = "night_start_hour"
CONF_NIGHT_END_HOUR: Final = "night_end_hour"
CONF_NIGHT_ON_WEEKENDS: Final = "night_on_weekends"
CONF_NIGHT_ON_HOLIDAYS: Final = "night_on_holidays"
DEFAULT_VAT_PERCENT: Final = 22.0
DEFAULT_MARGIN_EUR_PER_KWH: Final = 0.0
DEFAULT_FEES_EUR_PER_KWH: Final = 0.0
DEFAULT_GRID_EUR_PER_KWH: Final = 0.0
DEFAULT_NIGHT_START_HOUR: Final = 22
DEFAULT_NIGHT_END_HOUR: Final = 7
DEFAULT_NIGHT_ON_WEEKENDS: Final = True
DEFAULT_NIGHT_ON_HOLIDAYS: Final = True
# Options that feed the tariff; changing any of them triggers a cost rebuild.
TARIFF_OPTION_KEYS: Final = (
    CONF_VAT_PERCENT,
    CONF_MARGIN_EUR_PER_KWH,
    CONF_FEES_EUR_PER_KWH,
    CONF_GRID_DAY_EUR_PER_KWH,
    CONF_GRID_NIGHT_EUR_PER_KWH,
    CONF_NIGHT_START_HOUR,
    CONF_NIGHT_END_HOUR,
    CONF_NIGHT_ON_WEEKENDS,
    CONF_NIGHT_ON_HOLIDAYS,
)

UPDATE_INTERVAL: Final = timedelta(hours=1)
ROLLING_CACHE_DAYS: Final = 62
DATA_FRESH_THRESHOLD: Final = timedelta(hours=30)

API_BASE_URL: Final = "https://estfeed.elering.ee"
KEYCLOAK_TOKEN_URL: Final = "https://kc.elering.ee/realms/elering-sso/protocol/openid-connect/token"
RATE_LIMIT_SECONDS: Final = 5.0
TOKEN_REFRESH_MARGIN_SECONDS: Final = 30
REQUEST_TIMEOUT_SECONDS: Final = 30
MAX_EICS_PER_REQUEST: Final = 10
MAX_DAYS_PER_REQUEST: Final = 31
RECENT_REQUESTS_BUFFER_SIZE: Final = 5

ATTRIBUTION: Final = "Data provided by Elering Estfeed"


class Resolution(StrEnum):
    """API resolution values."""

    QUARTER_HOUR = "fifteen_min"
    HOUR = "one_hour"
    DAY = "one_day"
    WEEK = "one_week"
    MONTH = "one_month"


class Kind(StrEnum):
    """Metering data kind."""

    CONSUMPTION = "consumption"
    PRODUCTION = "production"


class CommodityType(StrEnum):
    """Estfeed commodity types."""

    ELECTRICITY = "ELECTRICITY"
    NATURAL_GAS = "NATURAL_GAS"
