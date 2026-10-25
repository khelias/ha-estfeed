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
# When off, the Nord Pool spot is ignored and the margin is the fixed energy
# price of a fixed-price contract (EUR/kWh incl. VAT).
CONF_USE_SPOT_PRICE: Final = "use_spot_price"
# Form-only field: the date the edited tariff starts to apply. The tariff it
# replaces is archived in CONF_TARIFF_HISTORY so earlier hours keep their price.
CONF_TARIFF_EFFECTIVE_FROM: Final = "tariff_effective_from"
# Stored list of closed tariff periods: [{"until": "YYYY-MM-DD", <tariff keys>}].
CONF_TARIFF_HISTORY: Final = "tariff_history"
# Fixed monthly charges incl. VAT (supplier + grid subscription). Spread evenly
# over the hours of each month and added to the consumption cost, so monthly
# cost totals match the bill.
CONF_MONTHLY_FEES_EUR: Final = "monthly_fees_eur"
DEFAULT_VAT_PERCENT: Final = 22.0
DEFAULT_MARGIN_EUR_PER_KWH: Final = 0.0
DEFAULT_FEES_EUR_PER_KWH: Final = 0.0
DEFAULT_GRID_EUR_PER_KWH: Final = 0.0
DEFAULT_NIGHT_START_HOUR: Final = 22
DEFAULT_NIGHT_END_HOUR: Final = 7
DEFAULT_NIGHT_ON_WEEKENDS: Final = True
DEFAULT_NIGHT_ON_HOLIDAYS: Final = True
DEFAULT_USE_SPOT_PRICE: Final = True
DEFAULT_MONTHLY_FEES_EUR: Final = 0.0
# Options that define one tariff (archived per period in CONF_TARIFF_HISTORY).
TARIFF_PRICING_KEYS: Final = (
    CONF_VAT_PERCENT,
    CONF_MARGIN_EUR_PER_KWH,
    CONF_FEES_EUR_PER_KWH,
    CONF_GRID_DAY_EUR_PER_KWH,
    CONF_GRID_NIGHT_EUR_PER_KWH,
    CONF_NIGHT_START_HOUR,
    CONF_NIGHT_END_HOUR,
    CONF_NIGHT_ON_WEEKENDS,
    CONF_NIGHT_ON_HOLIDAYS,
    CONF_USE_SPOT_PRICE,
    CONF_MONTHLY_FEES_EUR,
)
# Options that feed the cost statistics; changing any of them triggers a rebuild.
TARIFF_OPTION_KEYS: Final = (*TARIFF_PRICING_KEYS, CONF_TARIFF_HISTORY)
# Defaults of the tariff keys, used to snapshot a tariff that was never edited.
TARIFF_DEFAULTS: Final = {
    CONF_VAT_PERCENT: DEFAULT_VAT_PERCENT,
    CONF_MARGIN_EUR_PER_KWH: DEFAULT_MARGIN_EUR_PER_KWH,
    CONF_FEES_EUR_PER_KWH: DEFAULT_FEES_EUR_PER_KWH,
    CONF_GRID_DAY_EUR_PER_KWH: DEFAULT_GRID_EUR_PER_KWH,
    CONF_GRID_NIGHT_EUR_PER_KWH: DEFAULT_GRID_EUR_PER_KWH,
    CONF_NIGHT_START_HOUR: DEFAULT_NIGHT_START_HOUR,
    CONF_NIGHT_END_HOUR: DEFAULT_NIGHT_END_HOUR,
    CONF_NIGHT_ON_WEEKENDS: DEFAULT_NIGHT_ON_WEEKENDS,
    CONF_NIGHT_ON_HOLIDAYS: DEFAULT_NIGHT_ON_HOLIDAYS,
    CONF_USE_SPOT_PRICE: DEFAULT_USE_SPOT_PRICE,
    CONF_MONTHLY_FEES_EUR: DEFAULT_MONTHLY_FEES_EUR,
}

UPDATE_INTERVAL: Final = timedelta(hours=1)
ROLLING_CACHE_DAYS: Final = 62
# How far past now the hourly tick fetches spot prices: Elering publishes
# the next day around 14:00 local, so two days always covers what exists.
DAY_AHEAD_HORIZON_DAYS: Final = 2
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
