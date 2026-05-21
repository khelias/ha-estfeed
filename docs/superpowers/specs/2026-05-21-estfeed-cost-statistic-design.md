# Estfeed Cost Statistic — Design

**Date:** 2026-05-21
**Status:** Approved — ready for implementation plan

## Problem

The HA Energy dashboard cannot compute cost for the estfeed grid source because
estfeed publishes consumption and production as **external statistics**
(`estfeed:estfeed_consumption_<suffix>`), not live `sensor.*` entities. The
Energy dashboard's `entity_energy_price` path requires a live sensor with
`state_class: total_increasing`, so the only supported way to attach a price
to an external-statistic grid source is `stat_cost` / `stat_compensation` —
which must point to a pre-computed cumulative cost statistic in the user's
configured currency (EUR), not a price entity in EUR/kWh.

The integration must therefore publish its own cost and compensation
statistics computed from consumption/production × Nord Pool spot price.

## Goals

- Publish two new external statistics per electricity meter:
  - `estfeed:<slug>_cost_<suffix>` — cumulative cost in EUR
  - `estfeed:<slug>_compensation_<suffix>` — cumulative compensation in EUR
- Use Elering's open NPS API as the price source (same provider as the rest
  of the integration; public, no auth).
- Tariff formula: `final_price = spot × (1 + vat_percent/100) + margin_eur_per_kwh`
  with `vat_percent` and `margin_eur_per_kwh` configurable per config entry.
- Backfill cost/compensation over the same window as consumption/production
  (`backfill_months`).
- Gas meters: skip cost (no NPS gas price).

## Non-goals

- Full retail-bill modeling (separate day/night grid tariffs, multiple
  contract margins, fixed monthly fees) — out of scope; the design allows a
  follow-up to add per-time-window margins later if desired.
- Asymmetric production tariff (different VAT / margin for compensation than
  for cost). For now both share the same tariff; can be split into separate
  options later.
- Currency conversion. Stat unit follows `hass.config.currency` if set,
  otherwise `"EUR"`.

## Architecture

### New module: `nps.py`

`EleringNpsClient` — async client for `https://dashboard.elering.ee/api/nps/price`.

```python
class EleringNpsClient:
    def __init__(self, session: aiohttp.ClientSession) -> None: ...
    async def async_get_prices(
        self, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        """Return hourly EE-zone spot prices in EUR/kWh keyed by top-of-hour UTC."""
```

- Single endpoint: `GET /api/nps/price?start=<iso>&end=<iso>&fields=ee`.
- Response shape: `{"data": {"ee": [{"timestamp": <epoch>, "price": <eur_per_mwh>}, ...]}}`.
  Convert `price` from EUR/MWh to EUR/kWh (`/1000`) on ingest.
- Cache keys are top-of-hour UTC; NPS timestamps are epoch seconds and
  align to whole hours, matching the statistics bucket convention.
- In-memory dict cache keyed by top-of-hour UTC. Process-lifetime, no
  eviction (~60k entries at the 84-month max is trivial). Cache lookup
  first; only fetch the gap from NPS.
- Reuses the integration's shared `aiohttp` session.
- No retry on 5xx — surface the error to the caller as `NpsError`
  (defined in `nps.py` alongside the client); caller logs and skips.

### New module: `pricing.py`

Pure functions, no HA imports:

```python
def apply_tariff(
    spot_eur_per_kwh: float,
    vat_percent: float,
    margin_eur_per_kwh: float,
) -> float:
    return spot_eur_per_kwh * (1 + vat_percent / 100) + margin_eur_per_kwh

def make_tariff(
    vat_percent: float, margin_eur_per_kwh: float
) -> Callable[[float], float]:
    """Curry apply_tariff so compute_cost_rows can call tariff(spot)."""
    return lambda spot: apply_tariff(spot, vat_percent, margin_eur_per_kwh)

def compute_cost_rows(
    intervals: list[AccountingInterval],
    kind: Kind,
    prices: dict[datetime, float],
    tariff: Callable[[float], float],
    prior_sum: float,
) -> list[StatisticData]:
    """Build cumulative-sum cost rows.

    Buckets intervals into hours (matching compute_statistic_rows), then
    multiplies each bucket's kWh by tariff(prices[bucket_start]). Skips
    buckets where the price is missing. Output is sorted by start, with
    state == sum. Cost rows are rounded to 4 decimal places (€0.0001
    precision — finer than typical billing, coarser than float noise).
    """
```

### `statistics.py` additions

```python
@dataclass(frozen=True, slots=True)
class CostStream:
    statistic_id: str
    name: str
    unit: str       # currency code (e.g., "EUR")
    kind: Kind      # CONSUMPTION → cost, PRODUCTION → compensation

async def async_write_cost_statistics(
    hass: HomeAssistant,
    stream: CostStream,
    intervals: list[AccountingInterval],
    prices: dict[datetime, float],
    tariff: Callable[[float], float],
    prior_sum: float,
) -> float:
    """Compute cost rows, push via async_add_external_statistics, return new prior_sum."""
```

Cost stream metadata uses `unit_of_measurement` = currency code
(`hass.config.currency`, fallback `"EUR"`), `has_sum=True`,
`has_mean=False`, and `mean_type=NONE` when available. **No `unit_class`
is set** — `unit_class` is for energy/volume/mass unit conversion (see
`homeassistant.util.unit_conversion`), not currencies. HA's Energy
dashboard identifies cost-eligible statistics by matching the currency
code, not by `unit_class`.

### `coordinator.py` changes

- `__init__` constructs `self._nps = EleringNpsClient(session)` using
  `async_get_clientsession(hass)` (the coordinator already has `hass`;
  no new constructor param is needed).
- New helper `_build_tariff() -> Callable[[float], float]`: reads
  `vat_percent` and `margin_eur_per_kwh` from `self.options`, returns
  `pricing.make_tariff(...)`. Rebuilt per fetch so option changes take
  effect on the next tick without restart.
- New method `cost_streams_for(meter) -> list[CostStream]`:
  - Returns `[]` for non-ELECTRICITY commodity types.
  - Otherwise returns two CostStreams (cost, compensation) per meter.
- `_fetch_meter_window` gains the cost branch:

```python
if write_stats and meter.commodity_type == CommodityType.ELECTRICITY:
    try:
        prices = await self._nps.async_get_prices(cursor, chunk_end)
    except NpsError as err:
        self.last_nps_error = str(err)
        _LOGGER.warning("NPS fetch failed for %s..%s: %s", cursor, chunk_end, err)
        prices = None
    if prices is not None:
        self.last_nps_error = None
        tariff = self._build_tariff()
        for cost_stream in self.cost_streams_for(meter):
            relevant = ...  # same per-stream filtering as energy streams
            cost_prior_sums[cost_stream.statistic_id] = await async_write_cost_statistics(
                self.hass, cost_stream, relevant, prices, tariff,
                prior_sum=cost_prior_sums[cost_stream.statistic_id],
            )
```

- `_fetch_meter_window` gains a `cost_only: bool = False` parameter. When
  set, the method still fetches consumption/production intervals from
  Estfeed (cost computation needs them), but **skips writing** energy
  statistic rows. Only cost/compensation rows are written. Used by the
  options-change rebuild and the upgrade-time cost-initial-fill so already-
  correct energy statistics aren't rewritten from `prior_sum=0`.
- `last_nps_error: str | None` field added for diagnostics.
- New method `async_rebuild_cost()`: for each meter, calls
  `_fetch_meter_window` with `cost_only=True` and `force_start=True`
  over the configured `backfill_months` window. Used by both the
  options-change handler and the upgrade-time cost-initial-fill.
- **Note on rolling cache:** cost computation does not use the 62-day
  rolling interval cache; it operates on the freshly-fetched intervals
  for each chunk. The recorder is the source of truth for historical
  cost rows (read back via `get_last_statistics` for `prior_sum`). No
  `frozen_sum`-style machinery is needed because there is no
  cumulative-since-reset cost sensor in v1.

### `const.py` additions

```python
CONF_VAT_PERCENT: Final = "vat_percent"
CONF_MARGIN_EUR_PER_KWH: Final = "margin_eur_per_kwh"
DEFAULT_VAT_PERCENT: Final = 22.0
DEFAULT_MARGIN_EUR_PER_KWH: Final = 0.0
```

### `config_flow.py` changes

Options step adds two fields:
- `vat_percent` (number selector, default 22.0, min 0, max 100, step 0.1)
- `margin_eur_per_kwh` (number selector, default 0.0, min -1.0, max 1.0, step 0.001)

### `__init__.py` changes

- `_async_options_updated`: detect VAT% / margin changes by comparing old
  vs new options; if either changed, schedule
  `coordinator.async_rebuild_cost()` as a background task via
  `hass.async_create_background_task`. That method walks every meter via
  `_fetch_meter_window(start, end, write_stats=True, cost_only=True,
  force_start=True)` over the configured `backfill_months` window. The
  NPS cache absorbs the repeated price fetches, so a rebuild is cheap
  even at 84 months.

### `diagnostics.py` additions

Surface:
- `cost_stream_ids`: list of statistic_ids for cost/compensation per meter
- `last_nps_error`: same shape as existing `last_meter_errors`
- `nps_cache_size`: int, for cache-warmth observability

### `translations/en.json`, `et.json`

Add labels for `vat_percent` and `margin_eur_per_kwh` options fields. No
new sensor name keys — cost is published as an external statistic only;
users wire it via the Energy dashboard's `stat_cost` / `stat_compensation`
dropdowns. A live `sensor.estfeed_cost_*` mirror is explicitly out of
scope for v1.

## Data flow per tick

For one electricity meter, one hourly tick, one 31-day chunk:

1. `EstfeedClient.get_metering_data(chunk_start, chunk_end, eics=[meter.eic])`
   returns intervals.
2. Existing path: write consumption + production cumulative-sum rows,
   update rolling cache.
3. New path (electricity only): fetch hourly prices for the same window via
   `EleringNpsClient` (cache-first), build tariff function, write cost +
   compensation cumulative-sum rows.
4. Per-stream `prior_sum` and `latest_seen` are derived from
   `get_last_statistics` exactly like the energy streams — no new state.

Backfill (`async_initial_backfill`) and warm-cache (`async_warm_cache`)
iterate every stream from both `streams_for` and `cost_streams_for`, so
they handle cost automatically.

## Edge cases

- **Missing price for some hours.** `compute_cost_rows` skips those
  buckets. Cumulative `prior_sum` advances only over hours we priced.
  Next tick fills the gap once NPS publishes.
- **15-min consumption resolution.** All four quarter-hours share the same
  hourly NPS price; bucket-by-hour summation handles this transparently.
- **`hass.config.currency` unset.** Default to `"EUR"`, log once at setup.
- **Currency change after install.** Out of scope for v1; user re-installs
  the integration. (Document in README.)
- **NPS API outage on the backfill path.** Energy streams complete
  successfully; cost stream gets an empty rebuild. The next normal tick
  starts catching up. Logged as a warning, surfaced in diagnostics.
- **VAT or margin set to extreme values.** Validated by config flow ranges
  (VAT 0–100%, margin ±€1/kWh). No additional clamping.

## Testing

### Unit tests — pure, no HA

- `pricing.apply_tariff`: VAT-only, margin-only, both, negative margin, zero values.
- `pricing.compute_cost_rows`: single hour, sub-hourly 15-min bucketing,
  missing price for one hour (skipped), `None` interval value (skipped),
  `prior_sum` carry-forward, gas-style m³ values (still produce rows since
  tariff is unit-agnostic — gas meters never call this path in practice but
  the function shouldn't crash if it gets one).
- `nps.EleringNpsClient.async_get_prices`: happy path, cache hit (no HTTP
  call), partial cache miss, 5xx error raises `NpsError`, empty range.

### Integration tests — coordinator with mocked clients

- Single electricity meter, 1-day window → asserts cost + compensation
  rows written alongside consumption/production with correct cumulative
  values for a known fixture.
- Single gas meter, 1-day window → asserts NO cost/compensation writes
  and no NPS calls.
- Options change (VAT 22 → 24) → asserts cost rebuild triggered, energy
  streams untouched, cost cumulative reflects new VAT.
- NPS failure mid-tick → asserts energy streams still written, cost
  streams skipped, `last_nps_error` populated.

### Manual verification after deploy

1. Diagnostics shows `cost` and `compensation` stream IDs with non-zero
   last sums after first backfill.
2. HA Energy config UI exposes the new statistic IDs in the
   `stat_cost` / `stat_compensation` dropdowns.
3. After 1 hour: cost graph populated with reasonable euros given recent
   consumption × current spot.
4. Cost continues to update on the hourly tick without manual intervention.

## Rollout

- Ship as a minor-version bump; new options default safely (22% VAT, 0
  margin → net Estonian retail without margin, a reasonable default).
- On upgrade, existing entries detect "no cost statistic exists yet" via
  the same `get_last_statistics` probe used for energy streams. If
  missing, schedule a **cost-only initial fill** as a background task:
  `_fetch_meter_window(start=..., end=..., write_stats=True, cost_only=True,
  force_start=True)` over the configured `backfill_months` window. Energy
  stats are left untouched — they're already correct. Distinct from
  `async_initial_backfill`, which rewrites everything from `prior_sum=0`
  and is only safe on first install.
- README gains a section on configuring the Energy dashboard against the
  new `estfeed:<slug>_cost_<suffix>` statistic.

## Open questions

None — all clarifications resolved during brainstorming (price source,
tariff formula, compensation symmetry, backfill window).
