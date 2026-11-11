<script>
  import Skeleton from '../components/Skeleton.svelte';
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
          trip.start && { ...trip.start, colour: 'muted', label: 'Start' },
          trip.end && { ...trip.end, colour: 'accent', label: 'End' },
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
  <h1>Trips</h1>
  <span class="small muted">{monthLabel}</span>
</header>

{#if error}
  <div class="card">Could not load trips: {error}</div>
{:else if !data}
  <Skeleton label="Loading trips" />
{:else}
  <section class="strip boxed" aria-label="This month">
    <div class="readout"><span class="label">Distance this month</span><span class="value">{num(toUnit(s?.month_km, prefs.distance))}<span class="unit">{unit}</span></span></div>
    <div class="readout"><span class="label">Trips</span><span class="value">{num(s?.month_trips)}</span></div>
    <div class="readout"><span class="label">Time driving</span><span class="value">{duration(s?.month_minutes)}</span><span class="note">door open to locked</span></div>
    <div class="readout">
      <span class="label">Economy</span>
      <span class="value">{economy(s?.economy_l100, prefs.economy)}<span class="unit">{economyUnit(prefs.economy)}</span></span>
      <span class="note">{s?.economy_l100 ? `last 90 days, ${s.economy_trips} trips` : 'needs a few trips with fuel readings'}</span>
    </div>
  </section>

  {#if trips.length === 0}
    <div class="card empty">
      <h2>No trips yet</h2>
      <p class="muted">Trips appear a few minutes after the car is locked at the end of a drive.</p>
    </div>
  {:else}
    <div class="split">
      <ul class="card list" aria-label="Trips">
        {#each trips as t, i}
          <li><button class="row" class:active={i === selected} onclick={() => (selected = i)} aria-current={i === selected ? 'true' : undefined}>
            <span class="when">{dateTime(t.start_time)}{#if route(t)}<span class="route small muted">{route(t)}</span>{/if}</span>
            <span class="km num">{km(t.distance_km)} {unit}</span>
            <span class="small muted">{duration(t.minutes)}{t.fuel_used_l != null ? ` · ${num(t.fuel_used_l)} L` : ''}</span>
          </button></li>
        {/each}
      </ul>

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
          <div class="strip inset stats" style="--cols: 3">
            <div class="readout"><span class="label">Distance</span><span class="value">{km(trip.distance_km)}<span class="unit">{unit}</span></span></div>
            <div class="readout"><span class="label">Duration</span><span class="value">{duration(trip.minutes)}</span></div>
            <div class="readout"><span class="label">Average speed</span><span class="value">{speed ? num(toUnit(speed, prefs.distance)) : DASH}{#if speed}<span class="unit">{speedUnit(prefs.distance)}</span>{/if}</span></div>
            <div class="readout"><span class="label">Fuel used</span><span class="value">{trip.fuel_used_l != null ? num(trip.fuel_used_l) : DASH}{#if trip.fuel_used_l != null}<span class="unit">L</span>{/if}</span></div>
            <div class="readout" title="Share of the trip driven in ECO PRO (and ECO PRO+) mode">
              <span class="label">ECO PRO</span>
              <span class="value">{trip.eco_pro_pct != null ? `${num(trip.eco_pro_pct + (trip.eco_pro_plus_pct ?? 0))}%` : DASH}</span>
            </div>
            <div class="readout"><span class="label">Odometer</span><span class="value">{trip.end_odometer != null ? num(toUnit(trip.end_odometer, prefs.distance)) : DASH}{#if trip.end_odometer != null}<span class="unit">{unit}</span>{/if}</span></div>
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
  header { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px 16px; flex-wrap: wrap; }
  .empty h2 { margin-bottom: 6px; }
  .split { display: grid; gap: 16px; grid-template-columns: minmax(0, 340px) minmax(0, 1fr); align-items: start; }
  @media (max-width: 960px) { .split { grid-template-columns: 1fr; } }
  .list { list-style: none; margin: 0; padding: 4px 0; max-height: 640px; overflow-y: auto; }
  .list li + li { border-top: 1px solid var(--line); }
  .row { all: unset; box-sizing: border-box; width: 100%; cursor: pointer; display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; padding: 12px 20px; min-height: 44px; }
  .row:hover { background: var(--card-2); }
  .row.active { background: var(--card-3); }
  .row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .when { font-weight: 500; display: flex; flex-direction: column; gap: 2px; }
  .route { font-weight: 400; }
  .km { text-align: right; }
  .detail { display: flex; flex-direction: column; gap: 16px; }
  .detail .card-head { margin-bottom: 0; }
  .note { margin-top: -8px; }
  .nomap { padding: 28px; text-align: center; border: 1px dashed var(--line-2); border-radius: 8px; }
  .stats .value { font-size: 18px; }
</style>
