"""Tests for the Estfeed config flow."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.estfeed.api import (
    EstfeedAuthError,
    MeteringPoint,
    Period,
)
from custom_components.estfeed.const import (
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
    CONF_TARIFF_EFFECTIVE_FROM,
    CONF_TARIFF_HISTORY,
    CONF_USE_SPOT_PRICE,
    CONF_VAT_PERCENT,
    DEFAULT_MARGIN_EUR_PER_KWH,
    DEFAULT_VAT_PERCENT,
    DOMAIN,
    CommodityType,
)


def _meter() -> MeteringPoint:
    return MeteringPoint(
        eic="38ZEE-00720089-N",
        commodity_type=CommodityType.ELECTRICITY,
        periods=[Period(start=datetime(2019, 7, 27, 21, tzinfo=UTC), end=None)],
    )


async def _setup_recorder(hass) -> None:
    """Set up the recorder so config-flow init doesn't fail on its dependency."""
    from homeassistant.components import recorder
    from homeassistant.helpers import recorder as recorder_helper

    with patch("homeassistant.components.recorder.ALLOW_IN_MEMORY_DB", True):
        if recorder.DOMAIN not in hass.data:
            recorder_helper.async_initialize_recorder(hass)
        assert await async_setup_component(
            hass,
            recorder.DOMAIN,
            {recorder.DOMAIN: {"db_url": "sqlite://", "commit_interval": 0}},
        )
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_user_step_happy_path(hass):
    await _setup_recorder(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.estfeed.config_flow.EstfeedClient.list_metering_points",
        new=AsyncMock(return_value=[_meter()]),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CLIENT_ID: "cid",
                CONF_CLIENT_SECRET: "csec",
                CONF_FRIENDLY_NAME: "Home",
            },
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Home"
    assert result2["data"] == {
        CONF_CLIENT_ID: "cid",
        CONF_CLIENT_SECRET: "csec",
        CONF_FRIENDLY_NAME: "Home",
    }


@pytest.mark.asyncio
async def test_user_step_bad_credentials_shows_form_error(hass):
    await _setup_recorder(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.estfeed.config_flow.EstfeedClient.list_metering_points",
        new=AsyncMock(side_effect=EstfeedAuthError("bad")),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CLIENT_ID: "x",
                CONF_CLIENT_SECRET: "y",
                CONF_FRIENDLY_NAME: "Home",
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_step_rejects_colliding_friendly_name(hass):
    """Entity unique_ids and statistic_ids derive from the slugified friendly
    name; a second entry whose name slugifies identically would silently
    collide. The flow must reject it with slug_in_use."""
    await _setup_recorder(hass)
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "one", CONF_CLIENT_SECRET: "s", CONF_FRIENDLY_NAME: "My Home"},
        unique_id="one",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.estfeed.config_flow.EstfeedClient.list_metering_points",
        new=AsyncMock(return_value=[_meter()]),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            # Different API key, but "my home" slugifies to the same "my_home".
            user_input={
                CONF_CLIENT_ID: "two",
                CONF_CLIENT_SECRET: "s2",
                CONF_FRIENDLY_NAME: "my HOME",
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "slug_in_use"}


@pytest.mark.asyncio
async def test_user_step_accepts_same_entry_updating_name(hass):
    """Re-validating with the same client_id (unique_id match) aborts via
    already_configured before the slug check can false-positive."""
    await _setup_recorder(hass)
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "one", CONF_CLIENT_SECRET: "s", CONF_FRIENDLY_NAME: "Home"},
        unique_id="one",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.estfeed.config_flow.EstfeedClient.list_metering_points",
        new=AsyncMock(return_value=[_meter()]),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_CLIENT_ID: "one",
                CONF_CLIENT_SECRET: "s",
                CONF_FRIENDLY_NAME: "Home",
            },
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_reauth_flow_replaces_credentials(hass):
    await _setup_recorder(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "old", CONF_CLIENT_SECRET: "old", CONF_FRIENDLY_NAME: "Home"},
        unique_id="old",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.estfeed.config_flow.EstfeedClient.list_metering_points",
        new=AsyncMock(return_value=[_meter()]),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_CLIENT_ID: "new", CONF_CLIENT_SECRET: "new"},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data[CONF_CLIENT_ID] == "new"
    assert entry.data[CONF_CLIENT_SECRET] == "new"


@pytest.mark.asyncio
async def test_options_flow_opens_without_setting_config_entry(hass):
    """Regression: modern HA's OptionsFlow.config_entry is read-only.
    The flow must not try to assign self.config_entry in __init__."""
    await _setup_recorder(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "c", CONF_CLIENT_SECRET: "s", CONF_FRIENDLY_NAME: "Home"},
        options={},
        unique_id="c",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_persists_vat_and_margin(hass):
    """Options form accepts VAT% and margin and stores them in entry.options."""
    await _setup_recorder(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "x", CONF_CLIENT_SECRET: "y", CONF_FRIENDLY_NAME: "Home"},
        options={},
        unique_id="x",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    submission = {
        "resolution": "one_hour",
        "backfill_months": 12,
        CONF_VAT_PERCENT: 24.0,
        CONF_MARGIN_EUR_PER_KWH: 0.015,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], submission)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_VAT_PERCENT] == 24.0
    assert entry.options[CONF_MARGIN_EUR_PER_KWH] == 0.015
    # Grid/fee fields fall back to their defaults when the form omits them.
    assert entry.options[CONF_GRID_DAY_EUR_PER_KWH] == 0.0
    assert entry.options[CONF_NIGHT_START_HOUR] == 22
    assert entry.options[CONF_NIGHT_ON_HOLIDAYS] is True


@pytest.mark.asyncio
async def test_options_flow_persists_grid_tariff(hass):
    await _setup_recorder(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "x", CONF_CLIENT_SECRET: "y", CONF_FRIENDLY_NAME: "Home"},
        options={},
        unique_id="x",
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    submission = {
        "resolution": "one_hour",
        "backfill_months": 12,
        CONF_VAT_PERCENT: 24.0,
        CONF_MARGIN_EUR_PER_KWH: 0.0047,
        CONF_FEES_EUR_PER_KWH: 0.0219,
        CONF_GRID_DAY_EUR_PER_KWH: 0.0369,
        CONF_GRID_NIGHT_EUR_PER_KWH: 0.021,
        CONF_NIGHT_START_HOUR: 23,
        CONF_NIGHT_END_HOUR: 7,
        CONF_NIGHT_ON_WEEKENDS: True,
        CONF_NIGHT_ON_HOLIDAYS: False,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], submission)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_GRID_DAY_EUR_PER_KWH] == 0.0369
    assert entry.options[CONF_GRID_NIGHT_EUR_PER_KWH] == 0.021
    assert entry.options[CONF_FEES_EUR_PER_KWH] == 0.0219
    assert entry.options[CONF_NIGHT_START_HOUR] == 23
    assert entry.options[CONF_NIGHT_ON_HOLIDAYS] is False


@pytest.mark.asyncio
async def test_options_flow_defaults_to_22_percent_vat_and_zero_margin(hass):
    await _setup_recorder(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "x", CONF_CLIENT_SECRET: "y", CONF_FRIENDLY_NAME: "Home"},
        options={},
        unique_id="x",
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    rendered = {
        k.schema: k.default()
        for k in schema.schema
        if callable(getattr(k, "default", None)) and hasattr(k, "schema")
    }
    assert rendered[CONF_VAT_PERCENT] == DEFAULT_VAT_PERCENT
    assert rendered[CONF_MARGIN_EUR_PER_KWH] == DEFAULT_MARGIN_EUR_PER_KWH


async def _open_options(hass, options):
    await _setup_recorder(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CLIENT_ID: "x", CONF_CLIENT_SECRET: "y", CONF_FRIENDLY_NAME: "Home"},
        options=options,
        unique_id="x",
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    return entry, result


_FIXED = {
    "resolution": "one_hour",
    "backfill_months": 12,
    CONF_USE_SPOT_PRICE: False,
    CONF_VAT_PERCENT: 24.0,
    CONF_MARGIN_EUR_PER_KWH: 0.129,
}


async def test_options_tariff_change_with_date_archives_previous_tariff(hass):
    entry, result = await _open_options(hass, dict(_FIXED))
    submission = {
        **_FIXED,
        CONF_USE_SPOT_PRICE: True,
        CONF_MARGIN_EUR_PER_KWH: 0.005,
        CONF_TARIFF_EFFECTIVE_FROM: "2027-01-01",
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], submission)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    history = entry.options[CONF_TARIFF_HISTORY]
    assert len(history) == 1
    assert history[0]["until"] == "2027-01-01"
    assert history[0][CONF_USE_SPOT_PRICE] is False
    assert history[0][CONF_MARGIN_EUR_PER_KWH] == 0.129
    assert CONF_TARIFF_EFFECTIVE_FROM not in entry.options
    assert entry.options[CONF_USE_SPOT_PRICE] is True


async def test_options_tariff_change_without_date_applies_to_all_history(hass):
    archived = {
        **_FIXED,
        CONF_TARIFF_HISTORY: [{CONF_MARGIN_EUR_PER_KWH: 0.2, "until": "2026-01-01"}],
    }
    entry, result = await _open_options(hass, archived)
    submission = {**_FIXED, CONF_MARGIN_EUR_PER_KWH: 0.13}
    await hass.config_entries.options.async_configure(result["flow_id"], submission)
    assert entry.options[CONF_TARIFF_HISTORY] == []


async def test_options_unchanged_tariff_keeps_history(hass):
    history = [{CONF_MARGIN_EUR_PER_KWH: 0.2, "until": "2026-01-01"}]
    entry, result = await _open_options(hass, {**_FIXED, CONF_TARIFF_HISTORY: history})
    submission = {**_FIXED, "backfill_months": 6, CONF_TARIFF_EFFECTIVE_FROM: "2026-10-16"}
    await hass.config_entries.options.async_configure(result["flow_id"], submission)
    assert entry.options[CONF_TARIFF_HISTORY] == history
    assert entry.options["backfill_months"] == 6


async def test_options_monthly_fee_change_with_date_is_a_tariff_period(hass):
    entry, result = await _open_options(hass, {**_FIXED, CONF_MONTHLY_FEES_EUR: 12.39})
    submission = {**_FIXED, CONF_MONTHLY_FEES_EUR: 15.0, CONF_TARIFF_EFFECTIVE_FROM: "2027-02-01"}
    await hass.config_entries.options.async_configure(result["flow_id"], submission)
    history = entry.options[CONF_TARIFF_HISTORY]
    assert history == [{**history[0], "until": "2027-02-01"}]
    assert history[0][CONF_MONTHLY_FEES_EUR] == 12.39
    assert entry.options[CONF_MONTHLY_FEES_EUR] == 15.0
