<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num, monthName, longDate, ago, toUnit, distUnit, DASH, money, pounds, economy, economyUnit, perDistance } from '../lib/format.js';
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

  const c = $derived(data?.costs);
  const lim = $derived(m?.limit);
  const routes = $derived(data?.routes ?? []);
  const PRICE_SOURCE = { local: 'median price near home', uk_average: 'UK weekly average (DESNZ)' };
  const ECON_SOURCE = { car_recent: "the car's recent economy", car_lifetime: "the car's lifetime economy", fillups: 'your fill-ups' };
  const limitPct = $derived(lim ? Math.min(100, (lim.used_km / lim.limit_km) * 100) : 0);
  const projPct = $derived(lim?.projected_km ? Math.min(100, (lim.projected_km / lim.limit_km) * 100) : 0);

  const dests = $derived(data?.destinations ?? []);
  const markers = $derived(
    dests.map((d, i) => ({
      lat: d.lat,
      lon: d.lon,
      colour: d.name ? 'ok' : 'accent',
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
  <h1>Insights</h1>
</header>

{#if error}
  <div class="card">Could not load insights: {error}</div>
{:else if !data}
  <div class="muted">Loading…</div>
{:else}
  <section class="strip boxed" aria-label="Mileage summary">
    <div class="readout">
      <span class="label">Estimated annual mileage</span>
      <span class="value">{perYear != null ? num(perYear) : DASH}<span class="unit">{unit} / year</span></span>
      <span class="note">{basis(m)}</span>
    </div>
    <div class="readout">
      <span class="label">Odometer</span>
      <span class="value">{m?.odometer_km != null ? num(toUnit(m.odometer_km, prefs.distance)) : DASH}<span class="unit">{unit}</span></span>
      <span class="note">{m?.built ? `built ${longDate(m.built)}` : ''}</span>
    </div>
    <div class="readout">
      <span class="label">{s?.trips_per_week != null ? 'Trips per week' : 'Trips recorded'}</span>
      <span class="value">{s ? num(s.trips_per_week ?? s.trips, s.trips_per_week != null ? 1 : 0) : DASH}</span>
      <span class="note">{s?.avg_trip_km != null ? `average ${num(toUnit(s.avg_trip_km, prefs.distance), 1)} ${unit}` : 'no trips yet'}</span>
    </div>
    <div class="readout">
      <span class="label">Short trips</span>
      <span class="value">{s?.short_trip_share != null ? `${num(s.short_trip_share * 100)}%` : DASH}</span>
      <span class="note">under {shortLimit}: the engine rarely warms up</span>
    </div>
  </section>

  <div class="card">
    <div class="card-head">
      <h2>Running costs</h2>
      <span class="small muted">fuel only, at today's prices</span>
    </div>
    {#if !c?.pence_per_km}
      <p class="muted">Needs the car's fuel figures (from BMW, within a few hours) or two fill-ups, plus fuel prices.</p>
    {:else}
      <div class="strip inset costs">
        <div class="readout"><span class="label">Per month</span><span class="value">{c.cost_per_month != null ? pounds(c.cost_per_month) : DASH}</span></div>
        <div class="readout"><span class="label">Per year</span><span class="value">{c.cost_per_year != null ? pounds(c.cost_per_year) : DASH}</span></div>
        <div class="readout"><span class="label">Per {prefs.distance === 'mi' ? 'mile' : 'km'}</span><span class="value">{num(perDistance(c.pence_per_km, prefs.distance), 1)}p</span></div>
        <div class="readout"><span class="label">Fuel a year</span><span class="value">{c.litres_per_year != null ? num(c.litres_per_year) : DASH}{#if c.litres_per_year != null}<span class="unit">L</span>{/if}</span></div>
      </div>
      <p class="small muted">
        {perYear != null ? `${num(perYear)} ${unit} a year` : 'Your mileage'} at {economy(c.economy_l100, prefs.economy)} {economyUnit(prefs.economy)} ({ECON_SOURCE[c.economy_source]})
        and {num(c.price_ppl, 1)}p/L ({PRICE_SOURCE[c.price_source]}).
      </p>
    {/if}
  </div>

  {#if lim}
    <div class="card">
      <div class="card-head">
        <h2>Mileage limit</h2>
        <span class="small muted">{longDate(lim.year_start)} – {longDate(lim.year_end)}</span>
      </div>
      <div class="gauge limit-bar" role="img" aria-label={`Used ${num(limitPct)}% of the yearly limit; projected ${num(projPct)}%`}>
        <div class="fill proj" style:width={`${projPct}%`} class:over={lim.on_track === false}></div>
        <div class="fill used" style:width={`${limitPct}%`}></div>
        <span class="tick" style:left="25%"></span><span class="tick" style:left="50%"></span><span class="tick" style:left="75%"></span>
      </div>
      <div class="limit-facts">
        <span><strong class="num">{num(toUnit(lim.used_km, prefs.distance))}</strong> <span class="muted">of {num(toUnit(lim.limit_km, prefs.distance))} {unit} used{lim.used_estimated ? ' (partly estimated)' : ''}</span></span>
        {#if lim.projected_km != null}
          <span class:over-text={lim.on_track === false} class:ok-text={lim.on_track}>
            On course for {num(toUnit(lim.projected_km, prefs.distance))} {unit}{lim.on_track === false ? `: ${num(toUnit(lim.projected_km - lim.limit_km, prefs.distance))} over` : ''}
          </span>
        {/if}
      </div>
      <p class="small muted">{lim.days_left} days left. {lim.remaining_km > 0 ? `That's about ${num(toUnit(lim.remaining_km / Math.max(1, lim.days_left / 7), prefs.distance))} ${unit} a week to stay within it.` : 'The limit is already used up.'}</p>
    </div>
  {/if}

  <div class="two">
    <div class="card">
      <div class="card-head"><h2>Distance per month</h2><span class="small muted">{unit}</span></div>
      {#if months.length}
        <div class="bars" role="list" aria-label="Distance per month">
          {#each months as x, i}
            <div class="bar-col" role="listitem">
              <span class="sr-only">{monthName(x.month)}: {num(toUnit(x.km, prefs.distance))} {unit}</span>
              <span class="bar-v" aria-hidden="true">{num(toUnit(x.km, prefs.distance))}</span>
              <div class="bar" class:now={i === months.length - 1} aria-hidden="true" style:height={`${Math.max(3, (x.km / maxMonth) * 110)}px`}></div>
            </div>
          {/each}
        </div>
        <div class="axis" aria-hidden="true">{#each months as x}<span>{monthName(x.month)}</span>{/each}</div>
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
        <div class="heat" role="img" aria-label={`Trips by day of week and hour. ${busiest ?? 'Not enough trips yet to show a pattern.'}`}>
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

  {#if routes.length}
    <div class="card">
      <div class="card-head"><h2>Regular journeys</h2><span class="small muted">by named place</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Journey</th><th class="r">Trips</th><th class="r">Average</th><th class="r">Total</th><th class="r">Fuel cost</th></tr></thead>
          <tbody>
            {#each routes as r}
              <tr>
                <td>
                  {#if r.from}{r.from} → {r.to}{:else}<span class="muted">Other trips</span>{/if}
                  {#if r.short && r.short === r.trips}<span class="tag">short</span>{/if}
                </td>
                <td class="r">{r.trips}</td>
                <td class="r">{num(toUnit(r.avg_km, prefs.distance), 1)} {unit} · {r.avg_minutes} min</td>
                <td class="r">{num(toUnit(r.km, prefs.distance))} {unit}</td>
                <td class="r">{r.cost != null ? `~${money(r.cost)}` : DASH}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if !routes.some((r) => r.from)}
        <p class="small muted">Name the places below (Home, Work…) to split your driving into journeys.</p>
      {:else}
        <p class="small muted">Fuel cost is estimated from distance at your usual economy and today's prices.</p>
      {/if}
    </div>
  {/if}

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
      {#if placeError}<p class="small bad-text" role="alert">{placeError}</p>{/if}
      <p class="small muted">Named places label your trips and the car's location on Overview.</p>
    {/if}
  </div>
{/if}

<style>
  header { display: flex; align-items: flex-end; }
  .two { display: grid; gap: 16px; align-items: start; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); }
  .heat-wrap { overflow-x: auto; }
  .heat { display: grid; grid-template-columns: 32px repeat(24, minmax(10px, 1fr)); gap: 2px; min-width: 330px; align-items: center; }
  .cell { aspect-ratio: 1; border-radius: 1px; background: var(--card-3); }
  .hh { font-size: 11px; text-align: left; }
  .dd { font-size: 11px; }
  .places { display: grid; gap: 16px; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); align-items: start; }
  @media (max-width: 900px) { .places { grid-template-columns: 1fr; } }
  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); min-height: 56px; }
  li:first-child { border-top: 0; }
  .pname { font-weight: 500; }
  .actions { display: flex; gap: 6px; }
  .name-form { display: flex; gap: 6px; width: 100%; }
  .name-form input { flex: 1; min-width: 0; }
  .costs { margin-bottom: 12px; }
  .limit-bar { height: 10px; margin: 6px 0 12px; }
  .limit-bar .used { background: var(--accent); }
  .limit-bar .proj { background: var(--proj); }
  .limit-bar .proj.over { background: var(--proj-over); }
  .limit-facts { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 20px; }
  .over-text { color: var(--warn); }
  .ok-text { color: var(--ok); }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 520px; }
  th { text-align: left; font-size: 12px; color: var(--muted); font-weight: 500; padding: 0 10px 10px; }
  td { padding: 11px 10px; border-top: 1px solid var(--line); }
  .r { text-align: right; }
  .tag { margin-left: 8px; }
</style>
