<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num, monthName, longDate, ago, toUnit, distUnit, DASH } from '../lib/format.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import MapView from '../components/MapView.svelte';

  let { vehicle } = $props();
  let data = $state(null);
  let error = $state(null);
  let editing = $state(null); // index of the destination being named
  let nameInput = $state('');
  let placeError = $state(null);

  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const m = $derived(data?.mileage);
  const s = $derived(data?.stats);
  const unit = $derived(distUnit(prefs.distance));
  const perYear = $derived(m?.estimate_km_per_year != null ? toUnit(m.estimate_km_per_year, prefs.distance) : null);
  const months = $derived(m?.monthly ?? []);
  const maxMonth = $derived(Math.max(1, ...months.map((x) => x.km)));
  const shortLimit = $derived(prefs.distance === 'mi' ? '3 mi' : '5 km');

  // Trips per weekday x hour, in this browser's time zone.
  const heat = $derived.by(() => {
    const grid = Array.from({ length: 7 }, () => Array(24).fill(0));
    for (const t of data?.trips ?? []) {
      const d = new Date(t.start);
      grid[(d.getDay() + 6) % 7][d.getHours()] += 1;
    }
    return grid;
  });
  const heatMax = $derived(Math.max(1, ...heat.flat()));
  const busiest = $derived.by(() => {
    const trips = data?.trips ?? [];
    if (trips.length < 5) return null;
    const byDay = heat.map((row) => row.reduce((a, b) => a + b, 0));
    const byHour = Array.from({ length: 24 }, (_, h) => heat.reduce((a, row) => a + row[h], 0));
    const day = byDay.indexOf(Math.max(...byDay));
    const hour = byHour.indexOf(Math.max(...byHour));
    return `Most trips start on ${DAYS[day]}s, and around ${String(hour).padStart(2, '0')}:00.`;
  });

  const dests = $derived(data?.destinations ?? []);
  const markers = $derived(
    dests.map((d, i) => ({
      lat: d.lat,
      lon: d.lon,
      colour: d.name ? '#3dd68c' : '#5aa2ff',
      radius: 6 + Math.min(6, d.visits),
      label: `${d.name ?? `Place ${i + 1}`} · ${d.visits} visit${d.visits === 1 ? '' : 's'}`,
    }))
  );

  function basis(mm) {
    if (!mm?.estimate_basis) return 'needs odometer readings';
    if (mm.estimate_basis === 'recent') return `from your driving over the last ${num(Math.min(365, mm.history_days))} days`;
    const since = mm.built ? new Date(mm.built).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) : 'new';
    return `lifetime average since ${since}. Your recent rate takes over after 4 weeks of data.`;
  }

  async function load() {
    try {
      data = await api.usage(vehicle.vin);
    } catch (e) {
      error = e.message;
    }
  }

  function startNaming(i) {
    editing = i;
    nameInput = dests[i].name ?? '';
    placeError = null;
  }

  async function saveName(d) {
    const name = nameInput.trim();
    if (!name) return;
    placeError = null;
    try {
      if (d.place_id) await api.renamePlace(d.place_id, name);
      else await api.addPlace({ name, lat: d.lat, lon: d.lon });
      editing = null;
      await load();
    } catch (e) {
      placeError = e.message;
    }
  }

  async function forget(d) {
    try {
      await api.deletePlace(d.place_id);
      await load();
    } catch (e) {
      placeError = e.message;
    }
  }

  onMount(load);
</script>

<header>
  <div>
    <div class="eyebrow plain">Mileage and usage</div>
    <h1>Insights</h1>
  </div>
</header>

{#if error}
  <div class="card">Could not load insights: {error}</div>
{:else if !data}
  <div class="muted">Loading…</div>
{:else}
  <div class="grid" style="--min: 160px">
    <div class="card tile wide">
      <span class="muted">Estimated annual mileage</span>
      <span class="num tile-v">{perYear != null ? num(perYear) : DASH} <span class="unit">{unit} / year</span></span>
      <span class="small muted">{basis(m)}</span>
    </div>
    <div class="card tile">
      <span class="muted">Odometer</span>
      <span class="num tile-v">{m?.odometer_km != null ? num(toUnit(m.odometer_km, prefs.distance)) : DASH} <span class="unit">{unit}</span></span>
      <span class="small muted">{m?.built ? `built ${longDate(m.built)}` : ''}</span>
    </div>
    <div class="card tile">
      <span class="muted">{s?.trips_per_week != null ? 'Trips per week' : 'Trips recorded'}</span>
      <span class="num tile-v">{s ? num(s.trips_per_week ?? s.trips, s.trips_per_week != null ? 1 : 0) : DASH}</span>
      <span class="small muted">{s?.avg_trip_km != null ? `average ${num(toUnit(s.avg_trip_km, prefs.distance), 1)} ${unit}` : 'no trips yet'}</span>
    </div>
    <div class="card tile">
      <span class="muted">Short trips</span>
      <span class="num tile-v">{s?.short_trip_share != null ? `${num(s.short_trip_share * 100)}%` : DASH}</span>
      <span class="small muted">under {shortLimit}: the engine rarely warms up</span>
    </div>
  </div>

  <div class="two">
    <div class="card">
      <div class="card-head"><h2>Distance per month</h2><span class="small muted">{unit}</span></div>
      {#if months.length}
        <div class="bars" role="img" aria-label="Distance per month">
          {#each months as x, i}
            <div class="bar-col">
              <span class="small muted">{num(toUnit(x.km, prefs.distance))}</span>
              <div class="bar" style:height={`${Math.max(3, (x.km / maxMonth) * 120)}px`} style:background={i === months.length - 1 ? 'var(--accent)' : 'var(--bar)'}></div>
              <span class="small muted">{monthName(x.month)}</span>
            </div>
          {/each}
        </div>
        {#if m.history_days < 60}
          <p class="small muted">Recording started {longDate(m.history_from)}, so this fills in month by month.</p>
        {/if}
      {:else}
        <p class="muted">Builds up as odometer readings arrive.</p>
      {/if}
    </div>

    <div class="card">
      <div class="card-head"><h2>When you drive</h2><span class="small muted">trip starts</span></div>
      <div class="heat-wrap">
        <div class="heat" role="img" aria-label="Trips by day of week and hour">
          <span></span>
          {#each Array(24) as _, h}<span class="hh small muted">{h % 6 === 0 ? String(h).padStart(2, '0') : ''}</span>{/each}
          {#each heat as row, d}
            <span class="dd small muted">{DAYS[d]}</span>
            {#each row as n, h}
              <span class="cell" style:background={n ? `rgba(90, 162, 255, ${0.25 + 0.75 * (n / heatMax)})` : undefined} title={`${DAYS[d]} ${String(h).padStart(2, '0')}:00, ${n} trip${n === 1 ? '' : 's'}`}></span>
            {/each}
          {/each}
        </div>
      </div>
      <p class="small muted">{busiest ?? 'Patterns show after a couple of weeks of driving.'}</p>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Places</h2><span class="small muted">where trips end</span></div>
    {#if !dests.length}
      <p class="muted">Places appear once trips have GPS positions.</p>
    {:else}
      <div class="places">
        <MapView {markers} height={300} label="Places your trips end" />
        <ul>
          {#each dests as d, i}
            <li>
              {#if editing === i}
                <form class="name-form" onsubmit={(e) => { e.preventDefault(); saveName(d); }}>
                  <!-- svelte-ignore a11y_autofocus -->
                  <input bind:value={nameInput} maxlength="60" placeholder="e.g. Home" aria-label="Place name" autofocus />
                  <button class="btn" type="submit">Save</button>
                  <button class="btn ghost" type="button" onclick={() => (editing = null)}>Cancel</button>
                </form>
              {:else}
                <div>
                  <div class="pname">{d.name ?? `Place ${i + 1}`}{#if !d.name}<span class="small muted"> · not named</span>{/if}</div>
                  <div class="small muted">{d.visits} visit{d.visits === 1 ? '' : 's'} · last {ago(d.last_visit)}</div>
                </div>
                <div class="actions">
                  <button class="btn" onclick={() => startNaming(i)}>{d.name ? 'Rename' : 'Name it'}</button>
                  {#if d.place_id}<button class="btn ghost" onclick={() => forget(d)} aria-label={`Forget ${d.name}`}>Forget</button>{/if}
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      </div>
      {#if placeError}<p class="small bad" role="alert">{placeError}</p>{/if}
      <p class="small muted">Named places label your trips and the car's location on Overview.</p>
    {/if}
  </div>
{/if}

<style>
  header { display: flex; align-items: flex-end; }
  .tile { display: flex; flex-direction: column; gap: 6px; padding: 20px; }
  .tile.wide { grid-column: span 2; }
  @media (max-width: 520px) { .tile.wide { grid-column: auto; } }
  .tile-v { font-size: 26px; }
  .two { display: grid; gap: 16px; align-items: start; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); }
  .card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 16px; }
  .bars { display: flex; align-items: flex-end; gap: 8px; height: 180px; }
  .bar-col { flex: 1 1 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 8px; height: 100%; min-width: 0; }
  .bar { width: 100%; max-width: 36px; border-radius: 6px 6px 3px 3px; }
  .heat-wrap { overflow-x: auto; }
  .heat { display: grid; grid-template-columns: 32px repeat(24, minmax(10px, 1fr)); gap: 3px; min-width: 330px; align-items: center; }
  .cell { aspect-ratio: 1; border-radius: 3px; background: #1a1f27; }
  .hh { font-size: 10px; text-align: left; }
  .dd { font-size: 11px; }
  .places { display: grid; gap: 16px; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); align-items: start; }
  @media (max-width: 900px) { .places { grid-template-columns: 1fr; } }
  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); min-height: 56px; }
  li:first-child { border-top: 0; }
  .pname { font-weight: 500; }
  .actions { display: flex; gap: 6px; }
  .name-form { display: flex; gap: 6px; width: 100%; }
  .name-form input { flex: 1; min-width: 0; font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line-2); border-radius: 10px; padding: 8px 10px; min-height: 40px; }
  .btn { border: 1px solid var(--line-2); background: var(--card-3); border-radius: 10px; padding: 7px 12px; cursor: pointer; min-height: 40px; font-size: 13px; }
  .btn:hover { border-color: var(--accent); }
  .btn.ghost { background: none; color: var(--muted); }
  .bad { color: #ffb4b4; }
</style>
