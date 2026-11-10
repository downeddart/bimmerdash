<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num, dateTime, time, duration, economy, economyUnit, DASH, toUnit, distUnit, speedUnit } from '../lib/format.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import MapView from '../components/MapView.svelte';

  let { vehicle } = $props();
  let data = $state(null);
  let error = $state(null);
  let selected = $state(0);

  const trips = $derived(data?.trips ?? []);
  const s = $derived(data?.summary);
  const trip = $derived(trips[selected] ?? null);
  const monthLabel = $derived(
    s ? new Date(s.month_start).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }) : ''
  );
  const markers = $derived(
    !trip
      ? []
      : [
          trip.start && { ...trip.start, colour: '#9ba3af', label: 'Start' },
          trip.end && { ...trip.end, colour: '#5aa2ff', label: 'End' },
        ].filter(Boolean)
  );
  const line = $derived(
    !trip ? [] : [trip.start, ...trip.route, trip.end].filter(Boolean)
  );
  const speed = $derived(
    trip && trip.minutes > 0 ? (trip.distance_km / trip.minutes) * 60 : null
  );

  // "Home → Work" when either end is a named place.
  function route(t) {
    if (!t.start_place && !t.end_place) return '';
    return `${t.start_place?.name ?? 'Elsewhere'} → ${t.end_place?.name ?? 'elsewhere'}`;
  }

  function km(v) {
    const d = toUnit(v, prefs.distance);
    return d == null ? DASH : d < 1 ? '< 1' : num(d, d < 10 ? 1 : 0);
  }
  const unit = $derived(distUnit(prefs.distance));

  onMount(() => {
    api.trips(vehicle.vin).then((d) => (data = d)).catch((e) => (error = e.message));
  });
</script>

<header>
  <div>
    <div class="eyebrow">{monthLabel}</div>
    <h1>Trips</h1>
  </div>
</header>

{#if error}
  <div class="card">Could not load trips: {error}</div>
{:else if !data}
  <div class="muted">Loading…</div>
{:else}
  <div class="grid" style="--min: 150px">
    <div class="card tile"><span class="muted">This month</span><span class="num tile-v">{num(toUnit(s?.month_km, prefs.distance))} <span class="unit">{unit}</span></span></div>
    <div class="card tile"><span class="muted">Trips</span><span class="num tile-v">{num(s?.month_trips)}</span></div>
    <div class="card tile"><span class="muted">Time driving</span><span class="num tile-v">{duration(s?.month_minutes)}</span><span class="small muted">door open to locked</span></div>
    <div class="card tile">
      <span class="muted">Economy</span>
      <span class="num tile-v">{economy(s?.economy_l100, prefs.economy)} <span class="unit">{economyUnit(prefs.economy)}</span></span>
      <span class="small muted">{s?.economy_l100 ? `last 90 days, ${s.economy_trips} trips` : 'needs a few trips with fuel readings'}</span>
    </div>
  </div>

  {#if trips.length === 0}
    <div class="card empty">
      <h2>No trips yet</h2>
      <p class="muted">Trips appear a few minutes after the car is locked at the end of a drive.</p>
    </div>
  {:else}
    <div class="split">
      <div class="card list" role="list">
        {#each trips as t, i}
          <button class="row" class:active={i === selected} onclick={() => (selected = i)} role="listitem" aria-current={i === selected}>
            <span class="when">{dateTime(t.start_time)}{#if route(t)}<span class="route small muted">{route(t)}</span>{/if}</span>
            <span class="km num">{km(t.distance_km)} {unit}</span>
            <span class="small muted">{duration(t.minutes)}{t.fuel_used_l != null ? ` · ${num(t.fuel_used_l)} L` : ''}</span>
          </button>
        {/each}
      </div>

      {#if trip}
        <div class="card detail">
          <div class="card-head">
            <div>
              <h2>{route(trip) || dateTime(trip.start_time)}</h2>
              <div class="small muted">{route(trip) ? `${dateTime(trip.start_time)}, ` : ''}{time(trip.start_time)} – {time(trip.end_time)}</div>
            </div>
          </div>
          {#if markers.length}
            <MapView {markers} {line} label="Trip start and end" />
            <div class="small muted note">Route is approximate: the car only reports its position every few minutes.</div>
          {:else}
            <div class="nomap muted small">No position was reported for this trip.</div>
          {/if}
          <div class="stats">
            <div><span class="muted small">Distance</span><span class="num">{km(trip.distance_km)} {unit}</span></div>
            <div><span class="muted small">Duration</span><span class="num">{duration(trip.minutes)}</span></div>
            <div><span class="muted small">Avg speed</span><span class="num">{speed ? `${num(toUnit(speed, prefs.distance))} ${speedUnit(prefs.distance)}` : DASH}</span></div>
            <div><span class="muted small">Fuel used</span><span class="num">{trip.fuel_used_l != null ? `${num(trip.fuel_used_l)} L` : DASH}</span></div>
            <div><span class="muted small">Odometer</span><span class="num">{trip.end_odometer != null ? `${num(toUnit(trip.end_odometer, prefs.distance))} ${unit}` : DASH}</span></div>
          </div>
          {#if trip.fuel_used_l != null && trip.distance_km < 50}
            <div class="small muted">Fuel is reported in whole litres, so short trips are rough.</div>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
{/if}

<style>
  header { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
  .tile { display: flex; flex-direction: column; gap: 6px; padding: 20px; }
  .tile-v { font-size: 26px; }
  .empty h2 { margin-bottom: 6px; }
  .split { display: grid; gap: 16px; grid-template-columns: minmax(0, 340px) minmax(0, 1fr); align-items: start; }
  @media (max-width: 960px) { .split { grid-template-columns: 1fr; } }
  .list { padding: 8px; display: flex; flex-direction: column; gap: 2px; max-height: 640px; overflow-y: auto; }
  .row { all: unset; box-sizing: border-box; cursor: pointer; display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; padding: 12px 14px; border-radius: 12px; min-height: 44px; }
  .row:hover { background: var(--card-2); }
  .row.active { background: var(--card-3); }
  .row:focus-visible { outline: 2px solid var(--accent); }
  .when { font-weight: 500; display: flex; flex-direction: column; gap: 2px; }
  .route { font-weight: 400; }
  .km { text-align: right; }
  .detail { display: flex; flex-direction: column; gap: 16px; }
  .card-head { display: flex; justify-content: space-between; gap: 12px; }
  .note { margin-top: -8px; }
  .nomap { padding: 28px; text-align: center; border: 1px dashed var(--line-2); border-radius: 14px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
  .stats > div { display: flex; flex-direction: column; gap: 4px; padding: 12px 14px; border-radius: 12px; background: var(--card-2); }
  .stats .num { font-size: 18px; }
</style>
