<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { ago, num, title, shortDate, DASH, dist, distUnit, measure } from '../lib/format.js';
  import { prefs } from '../lib/prefs.svelte.js';
  import { placeAt } from '../lib/geo.js';
  import MapView from '../components/MapView.svelte';

  let { vehicle } = $props();
  let o = $state(null);
  let weeks = $state([]);
  let imageOk = $state(true);
  let places = $state([]);
  let health = $state(null);
  const nextService = $derived(health?.services?.find((x) => x.due) ?? null);
  const healthText = $derived.by(() => {
    if (!health) return null;
    if (health.issues) return `${health.issues} to check`;
    if (health.due_soon) return 'Service due soon';
    return 'All good';
  });
  const here = $derived(o?.location ? placeAt(places, o.location) : null);

  const name = $derived([vehicle.model_name, vehicle.body_type].filter(Boolean).join(' ') || 'Your car');
  const locked = $derived(
    o && (['LOCKED', 'SECURED'].includes(String(o.door_lock).toUpperCase()) ||
          String(o.doors).toUpperCase() === 'SECURED')
  );
  const headline = $derived(
    !o ? '' : o.is_moving ? 'Driving' : locked ? 'Parked and locked' : o.doors ? 'Parked' : 'Waiting for data'
  );
  const maxKm = $derived(Math.max(1, ...weeks.map((w) => w.km)));
  const thisWeek = $derived(weeks.length ? weeks[weeks.length - 1].km : null);
  // Fuel ring: circumference of r=62 is 389.6
  const fuelDash = $derived(o?.fuel_percent != null ? (o.fuel_percent / 100) * 389.6 : 0);

  const statusItems = $derived(
    !o
      ? []
      : [
          { label: 'Doors', value: o.door_lock ?? o.doors, good: locked },
          { label: 'Windows', value: o.windows_closed == null ? null : o.windows_closed ? 'Closed' : 'Open', good: o.windows_closed },
          { label: 'Boot', value: o.trunk_open == null ? null : o.trunk_open ? 'Open' : 'Closed', good: o.trunk_open === false },
          {
            label: 'Tyres',
            // psi, front left / front right / rear left / rear right
            value: o.tyres
              ? ['front_left', 'front_right', 'rear_left', 'rear_right']
                  .map((p) => (o.tyres[p] ? num(o.tyres[p].value * 0.145038) : DASH))
                  .join(' · ') + ' psi'
              : null,
            good: o.tyres ? !['low', 'very_low'].includes(health?.tyres?.state) : null,
            raw: true,
          },
          {
            label: 'Check control',
            value: o.check_control_count == null ? null : o.check_control_count === 0 ? 'No messages' : `${o.check_control_count} message(s)`,
            good: o.check_control_count === 0,
          },
          {
            label: '12 V battery',
            value: o.battery_12v ? `${num(o.battery_12v.value, 1)} ${o.battery_12v.unit ?? 'V'}` : null,
            good: o.battery_12v ? o.battery_12v.value >= 12.2 : null,
          },
        ]
  );

  async function load() {
    const [ov, wk] = await Promise.all([api.overview(vehicle.vin), api.weekly(vehicle.vin, 8)]);
    o = ov;
    weeks = wk;
    api.places().then((p) => (places = p)).catch(() => {});
    api.health(vehicle.vin).then((x) => (health = x)).catch(() => {});
  }

  onMount(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  });
</script>

{#if o}
  <header>
    <div>
      <div class="eyebrow plain">{[vehicle.model_name, vehicle.body_type, vehicle.series].filter(Boolean).join(' · ')}</div>
      <h1>{headline}</h1>
    </div>
    {#if locked}<span class="pill ok">All doors secured</span>{/if}
  </header>

  <section class="hero" aria-label="Your car">
    {#if vehicle.has_image && imageOk}
      <div class="floor" aria-hidden="true"></div>
      <img src={api.imageUrl(vehicle.vin)} alt={`${name}${vehicle.colour ? ' in ' + vehicle.colour : ''}`} onerror={() => (imageOk = false)} />
    {/if}
    <div class="hero-bar">
      <div class="hero-name">
        <span class="num">{name}</span>
        <span class="muted">{[vehicle.colour, `updated ${ago(o.updated_at)}`].filter(Boolean).join(' · ')}</span>
      </div>
      <div class="hero-stats">
        <div><span class="small muted">Fuel</span><span class="num" style:color="var(--fuel)">{o.fuel_percent != null ? `${num(o.fuel_percent)}%` : DASH}</span></div>
        <div><span class="small muted">Range</span><span class="num">{measure(o.range, prefs.distance)}</span></div>
        <div><span class="small muted">Odometer</span><span class="num">{measure(o.odometer, prefs.distance)}</span></div>
      </div>
    </div>
  </section>

  <section class="grid" style="--min: 340px">
    <div class="card fuel">
      <div class="ring">
        <svg role="img" aria-label={o.fuel_percent != null ? `Fuel ${o.fuel_percent} percent` : 'Fuel level not reported yet'} width="148" height="148" viewBox="0 0 148 148">
          <circle cx="74" cy="74" r="62" fill="none" stroke="#1f242c" stroke-width="12" />
          {#if o.fuel_percent != null}
            <circle cx="74" cy="74" r="62" fill="none" stroke="var(--fuel)" stroke-width="12" stroke-linecap="round" stroke-dasharray={`${fuelDash} 389.6`} transform="rotate(-90 74 74)" />
          {/if}
        </svg>
        <div class="ring-label"><span class="num big">{o.fuel_percent != null ? `${num(o.fuel_percent)}%` : DASH}</span><span class="small muted">fuel</span></div>
      </div>
      <div class="fuel-facts">
        <div><span class="muted">Range</span><span class="num mid">{o.range ? dist(o.range.value, prefs.distance) : DASH} <span class="unit">{distUnit(prefs.distance)}</span></span></div>
        <div><span class="muted">In the tank</span><span class="num mid">{o.fuel_litres ? num(o.fuel_litres.value, 1) : DASH} <span class="unit">L</span></span></div>
      </div>
    </div>

    <div class="card location">
      <div class="loc-head">
        <h2>{here ? `At ${here.name}` : 'Where it is'}</h2>
        {#if o.location}<span class="small muted">heading {num(o.location.heading)}°</span>{/if}
      </div>
      {#if o.location}
        <MapView
          markers={[{ lat: o.location.lat, lon: o.location.lon, colour: '#5aa2ff', radius: 9, label: here?.name ?? 'Your car' }]}
          height={230}
          label="Car location"
        />
      {:else}
        <p class="muted">No GPS position yet. It normally arrives after a drive.{o.heading != null ? ` Last heading ${num(o.heading)}°.` : ''}</p>
      {/if}
    </div>
  </section>

  <section class="grid" aria-label="Key stats">
    <div class="card tile"><span class="muted">This week</span><span class="num tile-v">{thisWeek != null ? `${dist(thisWeek, prefs.distance)} ${distUnit(prefs.distance)}` : DASH}</span><span class="small muted">from odometer</span></div>
    <a class="card tile link" href="#/health">
      <span class="muted">Health</span>
      <span class="num tile-v" style:color={health?.issues ? 'var(--warn)' : undefined}>{healthText ?? DASH}</span>
      <span class="small muted">{nextService ? `${nextService.name} ${new Date(nextService.due).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' })}` : `next service ${measure(o.service_distance, prefs.distance)}`}</span>
    </a>
    <div class="card tile"><span class="muted">Odometer</span><span class="num tile-v">{o.odometer ? dist(o.odometer.value, prefs.distance) : DASH}</span><span class="small muted">{distUnit(prefs.distance)}</span></div>
    <div class="card tile"><span class="muted">Status</span><span class="num tile-v">{o.is_moving ? 'Moving' : 'Parked'}</span><span class="small muted">updated {ago(o.updated_at)}</span></div>
  </section>

  <section class="grid" style="--min: 380px">
    <div class="card">
      <div class="card-head"><h2>Distance per week</h2><span class="small muted">{distUnit(prefs.distance)}, last 8 weeks</span></div>
      {#if weeks.length}
        <div class="bars" role="img" aria-label="Weekly distance bar chart">
          {#each weeks as w, i}
            <div class="bar-col">
              <span class="small muted">{dist(w.km, prefs.distance)}</span>
              <div class="bar" style:height={`${Math.max(2, (w.km / maxKm) * 110)}px`} style:background={i === weeks.length - 1 ? 'var(--accent)' : 'var(--bar)'}></div>
              <span class="small muted">{shortDate(w.week_start)}</span>
            </div>
          {/each}
        </div>
      {:else}
        <p class="muted">Builds up as odometer readings arrive.</p>
      {/if}
    </div>

    <div class="card">
      <h2>Car status</h2>
      <div class="grid status" style="--min: 170px">
        {#each statusItems as s}
          <div class="status-item">
            <span class="dot" style:background={s.value == null ? '#4b5563' : s.good ? 'var(--ok)' : 'var(--warn)'}></span>
            <div><span class="small muted">{s.label}</span><span class="status-v">{s.value == null ? 'Not reported yet' : s.raw ? s.value : title(s.value)}</span></div>
          </div>
        {/each}
      </div>
    </div>
  </section>
{:else}
  <div class="muted">Loading…</div>
{/if}

<style>
  .location { display: flex; flex-direction: column; gap: 14px; }
  .loc-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
  header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px; }
  header > div { display: flex; flex-direction: column; gap: 6px; }

  .hero { position: relative; min-height: 420px; border-radius: 24px; overflow: hidden; border: 1px solid var(--line-2); background: #1a2029; }
  .floor { position: absolute; left: 50%; bottom: 112px; width: min(78%, 760px); height: 46px; transform: translateX(-50%); border-radius: 50%; background: #262e3a; }
  .hero img { position: absolute; left: 50%; bottom: 96px; transform: translateX(-50%); width: min(86%, 700px); height: auto; }
  .hero-bar { position: absolute; left: 0; right: 0; bottom: 0; padding: 22px 24px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end; gap: 16px; background: #12161c; border-top: 1px solid var(--line-2); }
  .hero-name { display: flex; flex-direction: column; gap: 4px; }
  .hero-name .num { font-size: 24px; }
  .hero-stats { display: flex; gap: 28px; flex-wrap: wrap; }
  .hero-stats > div { display: flex; flex-direction: column; gap: 2px; }
  .hero-stats .num { font-size: 20px; }

  .fuel { display: flex; flex-wrap: wrap; gap: 28px; align-items: center; }
  .ring { position: relative; width: 148px; height: 148px; flex: none; }
  .ring-label { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  .big { font-size: 34px; }
  .mid { font-size: 26px; }
  .fuel-facts { display: flex; flex-direction: column; gap: 18px; flex: 1 1 160px; }
  .fuel-facts > div { display: flex; flex-direction: column; gap: 2px; }

  .location { display: flex; flex-direction: column; gap: 10px; }
  .location p { margin: 0; }

  .tile { display: flex; flex-direction: column; gap: 8px; padding: 18px 20px; border-radius: 16px; }
  .tile-v { font-size: 26px; }
  a.tile { color: var(--text); }
  a.tile:hover { border-color: var(--line-2); }

  .card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 20px; }
  .bars { display: flex; align-items: flex-end; gap: 10px; height: 170px; }
  .bar-col { flex: 1 1 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 8px; height: 100%; }
  .bar { width: 100%; max-width: 36px; border-radius: 6px 6px 3px 3px; }

  .status { margin-top: 18px; gap: 12px; }
  .status-item { display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 12px; background: var(--card-2); }
  .status-item > div { display: flex; flex-direction: column; gap: 2px; }
  .status-v { font-weight: 600; }

  @media (max-width: 600px) {
    .hero { min-height: 340px; }
    .hero img { bottom: 150px; width: 92%; }
    .floor { bottom: 160px; }
  }
</style>
