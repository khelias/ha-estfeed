"""Config flow for the Estfeed integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import DateSelector
from homeassistant.util import dt as dt_util

from .api import EstfeedAuthError, EstfeedClient, EstfeedError
from .const import (
    CONF_BACKFILL_MONTHS,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FEES_EUR_PER_KWH,
    CONF_FRIENDLY_NAME,
    CONF_GRID_DAY_EUR_PER_KWH,
    CONF_GRID_NIGHT_EUR_PER_KWH,
    CONF_MARGIN_EUR_PER_KWH,
    CONF_MONTHLY_FEES_EUR,
    CONF_NIGHT_END_HOUR,
    CONF_NIGHT_ON_HOLIDAYS,
    CONF_NIGHT_ON_WEEKENDS,
    CONF_NIGHT_START_HOUR,
    CONF_RESOLUTION,
    CONF_TARIFF_EFFECTIVE_FROM,
    CONF_TARIFF_HISTORY,
    CONF_USE_SPOT_PRICE,
    CONF_VAT_PERCENT,
    DEFAULT_BACKFILL_MONTHS,
    DEFAULT_FEES_EUR_PER_KWH,
    DEFAULT_FRIENDLY_NAME,
    DEFAULT_GRID_EUR_PER_KWH,
    DEFAULT_MARGIN_EUR_PER_KWH,
    DEFAULT_MONTHLY_FEES_EUR,
    DEFAULT_NIGHT_END_HOUR,
    DEFAULT_NIGHT_ON_HOLIDAYS,
    DEFAULT_NIGHT_ON_WEEKENDS,
    DEFAULT_NIGHT_START_HOUR,
    DEFAULT_USE_SPOT_PRICE,
    DEFAULT_VAT_PERCENT,
    DOMAIN,
    MAX_BACKFILL_MONTHS,
    MIN_BACKFILL_MONTHS,
    TARIFF_DEFAULTS,
    TARIFF_PRICING_KEYS,
    Resolution,
)
from .pricing import insert_tariff_period
from .utils import slugify

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
        vol.Required(CONF_FRIENDLY_NAME, default=DEFAULT_FRIENDLY_NAME): str,
    }
)


class EstfeedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Estfeed."""

    VERSION = 1

    def _slug_in_use(self, slug: str) -> bool:
        """True if another entry's friendly name slugifies to ``slug``.

        Entity unique_ids and statistic_ids are derived from the slug, so a
        collision would silently merge two entries' entities/statistics.
        Config-flow uniqueness is per client_id, so names must be checked
        separately across ALL entries of the domain (not just those sharing
        this flow's unique_id).
        """
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if slugify(entry.data.get(CONF_FRIENDLY_NAME, entry.title)) == slug:
                return True
        return False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step where the user enters API credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._validate(user_input)
            except EstfeedAuthError:
                errors["base"] = "invalid_auth"
            except EstfeedError:
                errors["base"] = "cannot_connect"
            else:
                # unique_id (client_id) abort must run before the slug check
                # so re-adding an existing key aborts instead of tripping
                # over its own friendly name.
                await self.async_set_unique_id(user_input[CONF_CLIENT_ID])
                self._abort_if_unique_id_configured()
                if self._slug_in_use(slugify(user_input[CONF_FRIENDLY_NAME])):
                    errors["base"] = "slug_in_use"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_FRIENDLY_NAME], data=user_input
                    )

        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:  # noqa: ARG002
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            full = {**entry.data, **user_input}
            try:
                await self._validate(full)
            except EstfeedAuthError:
                errors["base"] = "invalid_auth"
            except EstfeedError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data=full, reason="reauth_successful"
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID, default=entry.data.get(CONF_CLIENT_ID, "")): str,
                vol.Required(CONF_CLIENT_SECRET): str,
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    async def _validate(self, user_input: dict[str, Any]) -> None:
        session = async_get_clientsession(self.hass)
        client = EstfeedClient(
            session=session,
            client_id=user_input[CONF_CLIENT_ID],
            client_secret=user_input[CONF_CLIENT_SECRET],
        )
        end = datetime.now(tz=UTC)
        start = end - timedelta(days=7)
        await client.list_metering_points(start, end)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:  # noqa: ARG004
        return EstfeedOptionsFlow()


class EstfeedOptionsFlow(OptionsFlow):
    """Handle Estfeed integration options.

    HA assigns ``self.config_entry`` automatically when instantiating the flow;
    in modern versions it's a read-only property, so don't override __init__.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(
                title="", data=_merge_tariff_history(current, user_input)
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RESOLUTION,
                    default=current.get(CONF_RESOLUTION, Resolution.HOUR.value),
                ): vol.In([Resolution.HOUR.value, Resolution.QUARTER_HOUR.value]),
                vol.Required(
                    CONF_BACKFILL_MONTHS,
                    default=current.get(CONF_BACKFILL_MONTHS, DEFAULT_BACKFILL_MONTHS),
                ): vol.All(int, vol.Range(min=MIN_BACKFILL_MONTHS, max=MAX_BACKFILL_MONTHS)),
                vol.Optional(
                    CONF_TARIFF_EFFECTIVE_FROM,
                    description={"suggested_value": dt_util.now().date().isoformat()},
                ): DateSelector(),
                vol.Required(
                    CONF_USE_SPOT_PRICE,
                    default=current.get(CONF_USE_SPOT_PRICE, DEFAULT_USE_SPOT_PRICE),
                ): bool,
                vol.Required(
                    CONF_VAT_PERCENT,
                    default=current.get(CONF_VAT_PERCENT, DEFAULT_VAT_PERCENT),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
                vol.Required(
                    CONF_MARGIN_EUR_PER_KWH,
                    default=current.get(CONF_MARGIN_EUR_PER_KWH, DEFAULT_MARGIN_EUR_PER_KWH),
                ): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
                vol.Required(
                    CONF_FEES_EUR_PER_KWH,
                    default=current.get(CONF_FEES_EUR_PER_KWH, DEFAULT_FEES_EUR_PER_KWH),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Required(
                    CONF_GRID_DAY_EUR_PER_KWH,
                    default=current.get(CONF_GRID_DAY_EUR_PER_KWH, DEFAULT_GRID_EUR_PER_KWH),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Required(
                    CONF_GRID_NIGHT_EUR_PER_KWH,
                    default=current.get(CONF_GRID_NIGHT_EUR_PER_KWH, DEFAULT_GRID_EUR_PER_KWH),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Required(
                    CONF_NIGHT_START_HOUR,
                    default=current.get(CONF_NIGHT_START_HOUR, DEFAULT_NIGHT_START_HOUR),
                ): vol.All(int, vol.Range(min=0, max=23)),
                vol.Required(
                    CONF_NIGHT_END_HOUR,
                    default=current.get(CONF_NIGHT_END_HOUR, DEFAULT_NIGHT_END_HOUR),
                ): vol.All(int, vol.Range(min=0, max=23)),
                vol.Required(
                    CONF_NIGHT_ON_WEEKENDS,
                    default=current.get(CONF_NIGHT_ON_WEEKENDS, DEFAULT_NIGHT_ON_WEEKENDS),
                ): bool,
                vol.Required(
                    CONF_NIGHT_ON_HOLIDAYS,
                    default=current.get(CONF_NIGHT_ON_HOLIDAYS, DEFAULT_NIGHT_ON_HOLIDAYS),
                ): bool,
                vol.Required(
                    CONF_MONTHLY_FEES_EUR,
                    default=current.get(CONF_MONTHLY_FEES_EUR, DEFAULT_MONTHLY_FEES_EUR),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1000.0)),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"history": _describe_history(current)},
        )


def _tariff_of(options: Mapping[str, Any]) -> dict[str, Any]:
    """The tariff keys of an options mapping, with defaults filled in."""
    return {key: options.get(key, TARIFF_DEFAULTS[key]) for key in TARIFF_PRICING_KEYS}


def _merge_tariff_history(current: Mapping[str, Any], user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn a submitted options form into stored options with tariff periods.

    When the tariff changed and an effective date was given, the tariff in force
    until then is archived so hours before that date keep their price. With no
    date the new tariff applies to the whole history and the archive is cleared.
    A submit that leaves the tariff unchanged keeps the archive as it was.
    """
    data = dict(user_input)
    raw_date = data.pop(CONF_TARIFF_EFFECTIVE_FROM, None)
    history = list(current.get(CONF_TARIFF_HISTORY) or [])
    if _tariff_of(data) != _tariff_of(current):
        if raw_date:
            effective_from = date.fromisoformat(str(raw_date))
            history = insert_tariff_period(history, _tariff_of(current), effective_from)
        else:
            history = []
    data[CONF_TARIFF_HISTORY] = history
    return data


def _describe_history(options: Mapping[str, Any]) -> str:
    """One line per archived tariff period, for the options form description."""
    lines = []
    start = "…"
    for period in sorted(options.get(CONF_TARIFF_HISTORY) or [], key=lambda p: str(p["until"])):
        kind = "spot" if period.get(CONF_USE_SPOT_PRICE, True) else "fixed"
        lines.append(
            f"{start} → {period['until']}: {kind}, margin {period.get('margin_eur_per_kwh')}, "
            f"monthly fees {period.get(CONF_MONTHLY_FEES_EUR, 0)}"
        )
        start = str(period["until"])
    lines.append(f"{start} → now: current")
    return "\n".join(lines)
