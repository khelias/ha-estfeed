# Estfeed for Home Assistant

[![Validate](https://img.shields.io/github/actions/workflow/status/tehisain/ha-estfeed/validate.yml?branch=main&label=validate)](https://github.com/tehisain/ha-estfeed/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Imports [Elering Estfeed](https://estfeed.elering.ee/) meter data into Home Assistant's Energy dashboard, with daily, monthly and cumulative sensors.

Polls hourly. Meter readings can arrive late; this is not a real-time power monitor.

## Installation

Requires Home Assistant 2024.12 or later.

1. In HACS, open **Custom repositories** and add [this repository](https://github.com/khelias/ha-estfeed) with type **Integration**. See the [HACS instructions](https://www.hacs.dev/docs/faq/custom_repositories/).
2. Install **Estfeed** and restart Home Assistant.
3. Open **Settings → Devices & services → Add integration** and search for **Estfeed**.
4. Enter the `client_id` and `client_secret` from an API key created in the [Estfeed customer portal](https://estfeed.elering.ee/), plus a name for this installation.

For manual installation, copy `custom_components/estfeed` into your Home Assistant `config/custom_components` directory, restart, then follow steps 3–4.

## Energy dashboard

Setup imports 12 months of history in the background. Options allow 1–84 months of 30 days each. Import time depends on the meters and available data.

Select these external statistics in the Energy dashboard's grid settings. `<name>` is the installation name in lowercase with underscores; `<suffix>` is the meter EIC's last four alphanumeric characters, in lowercase.

| Grid setting | Energy statistic | Optional cost statistic |
| --- | --- | --- |
| Consumption | `estfeed:<name>_consumption_<suffix>` | `estfeed:<name>_cost_<suffix>` |
| Return to grid | `estfeed:<name>_production_<suffix>` | `estfeed:<name>_compensation_<suffix>` |

For costs, choose **Use an entity tracking the total costs** and select the matching statistic. For gas, select the consumption statistic under **Gas consumption**. Gas uses m³ and has no derived costs.

Electricity costs and production compensation use the EE Nord Pool prices from [Elering](https://dashboard.elering.ee/). Quarter-hour prices are averaged per hour, then multiplied by hourly energy using a tariff that mirrors an Estonian electricity invoice:

```text
EUR/kWh = (spot + grid transfer + fees) × (1 + VAT / 100) + margin
```

- **Grid transfer** is time-of-use: a day rate and a night rate (EUR/kWh excl. VAT). Night runs from `night_start_hour` to `night_end_hour` in Home Assistant's time zone (default 22-07), and optionally covers whole weekends and public holidays (default on; holidays come from Home Assistant's configured country via the `holidays` package).
- **Fees** are the fixed per-kWh items quoted excl. VAT (renewable energy levy, security of supply, excise, balancing).
- **Margin** is the supplier's per-kWh margin, quoted incl. VAT, so it lands after the VAT multiplication. Can be negative.

Set these in the integration options. The defaults are **22% VAT** and 0 for everything else, which reduces to `spot × (1 + VAT / 100) + margin`; Estonia's [standard VAT rate is 24% from 1 July 2025](https://www.emta.ee/en/admin/content/handbook_article/39). Example for an Elektrilevi "Võrk 4" contract with Alexela (2026): grid day 0.0369, night 0.021, fees 0.0219, VAT 24, margin 0.0047. Check these settings against your contract. One tariff applies to consumption, production and all imported history. Changing any tariff option rebuilds the configured window's costs.

These EUR estimates leave out time-windowed supplier margins, monthly fixed charges and anything else not priced per kWh, and approximate quarter-hour pricing with the hourly mean. They are labelled EUR regardless of Home Assistant's currency setting. Use the dashboard's own price configuration if needed.

### Fixed-price contracts

[#fixed-price-contracts](#fixed-price-contracts)

On a fixed-price (non-exchange) contract, turn **off** "Exchange (spot) price contract" (`use_spot_price`) in the integration options. The spot price is then ignored and the tariff becomes:

```
(grid transfer + fees) × (1 + VAT%/100) + margin
```

where **margin** is your fixed energy price per kWh incl. VAT. Grid day/night rates, weekends and public holidays work exactly as above.

### Changing contracts (tariff periods)

When you edit the tariff, set **Tariff valid from** to the date the new contract starts. The tariff in force until then is archived as a period, so every hour before that date keeps the price it was billed at; only hours from that date on use the new tariff, and the cost history rebuild respects the periods. The date may lie in the future to schedule a switch. Leave the date empty only to correct a mistake for the whole history (this clears the archive). The options form lists the archived periods.

### Price now and bill forecast

Electricity meters also get `sensor.<n>_price_now` (EUR/kWh for the current hour under the tariff in force, refreshed hourly) and `sensor.<n>_bill_forecast` (this month's settled cost extrapolated to the whole month; until a full day of the month has settled it uses the previous month's rate).

### Monthly fees

**Monthly fees** (EUR incl. VAT: supplier and grid subscriptions) are spread evenly over the hours of each local month and added to the consumption cost statistic and cost sensors, so a month's cost on the Energy dashboard equals the bill. They are part of the tariff, so they are archived per tariff period and a change follows **Tariff valid from**. Compensation for production never includes them.

### Hourly price statistic

Electricity entries also publish `estfeed:<name>_price` (unit `EUR/kWh`, mean/min/max per hour): the full tariff price of every hour the integration has spot prices for, including the day-ahead hours Elering has already published, so price history is available from the first install rather than from whenever a price sensor started being recorded. Every hourly tick writes the hours that are new or changed, a restart or backfill writes the whole cached window, and a tariff option change rewrites it. Plot it with the core `statistics-graph` card or any card that reads `recorder/statistics_during_period`.

## Entities

Each meter gets:

- Consumption totals for today, yesterday, month to date and the previous month.
- Cumulative consumption since setup or the last reset, and a reset button.
- Matching production sensors and a reset button, disabled by default.
- For electricity, cost totals for today, yesterday, month to date and the previous month, priced with the same tariff as the cost statistics. The `hours_without_price` attribute counts hours whose spot price is not cached yet. Matching compensation sensors are disabled by default.
- A latest-interval timestamp and a data-fresh binary sensor (on when the newest cached interval is less than 30 hours old).

Periods follow Home Assistant's time zone. Find entity IDs under **Settings → Devices & services → Estfeed**; names vary with the installation, meter and language.

Cumulative baselines survive restarts. Totals use a 62-day cache plus saved contributions from older intervals. Outages longer than 62 days can leave gaps in the cumulative sensor. Moving a reset timestamp only recounts cached intervals and clears saved older contributions.

## Actions

Run these from **Developer tools → Actions**:

| Action | Fields | Effect |
| --- | --- | --- |
| `estfeed.backfill_history` | `months` (1–84, default 24), optional `entry_id` | Fetch and replace energy and cost statistics for the requested window. |
| `estfeed.set_cumulative_reset_at` | `reset_at`, optional `entry_id` | Set the cumulative baseline for consumption and production on all meters in the entry. |

Omitting `entry_id` targets every loaded Estfeed entry. A reset timestamp without a time-zone offset uses Home Assistant's time zone.

## Development

Use Python 3.14 or later, which current Home Assistant releases require, in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]' --config-settings editable_mode=compat
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts
mypy
pytest tests --cov=custom_components/estfeed --cov-fail-under=85
```

The compatibility flag lets Home Assistant discover the package during tests. To check the live API with your own credentials:

```bash
ESTFEED_CLIENT_ID=... ESTFEED_CLIENT_SECRET=... python -m scripts.smoke
```
