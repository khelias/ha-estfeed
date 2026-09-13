# Estfeed — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?logo=homeassistant&logoColor=white)](https://github.com/hacs/integration)
[![Validate](https://img.shields.io/github/actions/workflow/status/tehisain/ha-estfeed/validate.yml?branch=main&label=validate&logo=github)](https://github.com/tehisain/ha-estfeed/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/tehisain/ha-estfeed?color=blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/tehisain/ha-estfeed?color=blueviolet)](https://github.com/tehisain/ha-estfeed/commits/main)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg?logo=ruff)](https://github.com/astral-sh/ruff)

Home Assistant integration for Elering's [Estfeed](https://estfeed.elering.ee/) metering data API. Brings Estonian electricity (and gas) meter data into the **Energy Dashboard** with full historical backfill, plus lagging summary sensors for cards and automations.

> **Note:** Estfeed publishes intervals throughout the day, but each hour goes through a settling period before the kWh value is finalised — the integration polls hourly and replaces unsettled placeholders with real values once the grid operator confirms them. Most hours land within a few hours of midnight that closed them; some may take longer. Expect today's running total to grow in chunks rather than minute-by-minute, and don't treat it as real-time.

## Installation

### HACS (recommended)

1. Add this repository as a custom HACS repository (category: Integration).
2. Install "Estfeed" from HACS.
3. Restart Home Assistant.
4. Settings → Devices & Services → "+ Add Integration" → search "Estfeed".

## Configuration

You'll need a `client_id` and `client_secret` from your e-Elering customer portal:
1. Log in to https://kliendiportaal.elering.ee
2. Generate an API key. The portal shows you the `client_id` (UUID) and `client_secret`.
3. Paste both into the Estfeed integration setup form.

## Energy Dashboard wiring

After setup completes (and the backfill finishes — usually within 1–2 minutes), open Settings → Energy → Electricity grid → "Add consumption" and pick `estfeed:<your_name>_consumption_<eic_suffix>`. If you have solar, add the matching `_production_` stream as "Return to grid".

The integration writes external statistics with proper cumulative-sum semantics and a `last_reset` attribute on the cumulative-since-reset sensor, so HA's Energy dashboard handles resets without flagging them as counter rollbacks.

### Cost & compensation statistics

For electricity meters, the integration also publishes two derived external statistics in EUR:

- `estfeed:<your_name>_cost_<eic_suffix>` — cumulative cost of consumed energy
- `estfeed:<your_name>_compensation_<eic_suffix>` — cumulative compensation for produced energy

Both are computed by multiplying each hour's consumption/production by the matching Nord Pool spot price for the EE bidding zone (fetched from the Elering NPS API), then applying a configurable tariff that mirrors an Estonian electricity invoice:

```
(spot + grid transfer + fees) × (1 + VAT%/100) + margin
```

- **Grid transfer** is time-of-use: a day rate and a night rate (EUR/kWh excl. VAT). Night runs from `night_start_hour` to `night_end_hour` in HA's time zone (default 22-07), and optionally covers whole weekends and public holidays (default on; holidays come from HA's configured country via the `holidays` package).
- **Fees** are the fixed per-kWh items quoted excl. VAT (renewable energy levy, security of supply, excise, balancing).
- **Margin** is the supplier's per-kWh margin, quoted incl. VAT, so it lands after the VAT multiplication. Can be negative.

Defaults: VAT 22 %, everything else 0, which reduces to the previous `spot × VAT + margin` behaviour. Example for an Elektrilevi "Võrk 4" contract with Alexela (2026): grid day 0.0369, night 0.021, fees 0.0219, VAT 24, margin 0.0047. Adjust in the integration options.

### Hourly price statistic

Electricity entries also publish `estfeed:<your_name>_price` (unit `<currency>/kWh`, mean/min/max per hour): the full tariff price of every hour the integration has spot prices for, including the day-ahead hours Elering has already published, so price history is available from the first install rather than from whenever a price sensor started being recorded. Every hourly tick writes the hours that are new or changed, a restart or backfill writes the whole cached window, and a tariff option change rewrites it. Plot it with the core `statistics-graph` card or any card that reads `recorder/statistics_during_period`.

To wire them into the Energy dashboard:

1. Open Settings → Dashboards → Energy → "Grid consumption" for the existing `estfeed:<your_name>_consumption_<eic_suffix>` row.
2. Under "Use an entity tracking the total costs", select `estfeed:<your_name>_cost_<eic_suffix>`.
3. Repeat for "Return to grid" → pair `estfeed:<your_name>_production_<eic_suffix>` with `estfeed:<your_name>_compensation_<eic_suffix>`.

Changing VAT or margin in the integration options automatically rebuilds the cost/compensation history over the configured backfill window, so the dashboard reflects the new tariff retroactively.

Gas meters do not publish cost statistics (no spot-price source).

## Entities created

For each metering point:
- `sensor.<name>_consumption_today` (kWh — running total for the current local day; grows as new hourly intervals settle)
- `sensor.<name>_consumption_yesterday` (kWh)
- `sensor.<name>_consumption_month_to_date` (kWh)
- `sensor.<name>_consumption_previous_month` (kWh)
- `sensor.<name>_consumption_cumulative` (kWh — total since the last reset; baseline is captured at install so the sensor starts at 0 and counts forward)
- `sensor.<name>_production_today` / `_yesterday` / `_month_to_date` / `_previous_month` / `_cumulative` (kWh, **disabled by default** — enable in entity registry if you generate)
- `sensor.<name>_cost_today` / `_yesterday` / `_month_to_date` / `_previous_month` (EUR, electricity only — the cached hours priced with the same tariff as the cost statistics; `hours_without_price` attribute counts hours whose spot price is not cached yet)
- `sensor.<name>_compensation_today` / `_yesterday` / `_month_to_date` / `_previous_month` (EUR, **disabled by default**)
- `sensor.<name>_latest_interval` (timestamp, diagnostic)
- `binary_sensor.<name>_data_fresh` (diagnostic — `on` if newest interval is < 30 h old)
- `button.<name>_consumption_cumulative_reset` (re-captures the current cumulative as the new baseline, so the cumulative sensor returns to 0; the matching production button exists too and is disabled by default)

## Services

- `estfeed.backfill_history(months=24, entry_id=<uuid>)` — re-fetch and re-publish the last N months of statistics. Idempotent.

## Limitations

- Not real-time: hours need to settle before their kWh value is final (see the note at the top).
- API rate limit: 1 request per 5 seconds (per API key) — handled internally.

## Development

The `editable_mode=compat` flag avoids a setuptools/HA loader incompatibility where the default editable install creates a virtual path entry that HA's `async_get_custom_components` cannot iterate.

~~~bash
pip install -e . --config-settings editable_mode=compat
pip install pytest pytest-asyncio pytest-cov pytest-homeassistant-custom-component homeassistant aioresponses freezegun ruff mypy
pytest tests --cov=custom_components/estfeed
ruff check custom_components tests
mypy
~~~

For a live end-to-end check against your own API key:

~~~bash
ESTFEED_CLIENT_ID=... ESTFEED_CLIENT_SECRET=... python scripts/smoke.py
~~~
