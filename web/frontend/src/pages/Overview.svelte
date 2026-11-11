<script>
  import Skeleton from '../components/Skeleton.svelte';
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { ago, num, title, shortDate, DASH, dist, distUnit } from '../lib/format.js';
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
  // Everything that wants the owner's attention, most urgent first, each linking to where it's dealt with.
  const attention = $derived.by(() => {
    if (!o) return [];
    const out = [];
    const warnings = health?.warnings?.items?.length ?? 0;
    if (warnings) out.push({ text: `${warnings} warning light${warnings === 1 ? '' : 's'}`, tone: 'bad', href: '#/health' });
    for (const s of health?.services ?? []) {
      if (s.state === 'overdue') out.push({ text: `${s.name} overdue`, tone: 'bad', href: '#/health' });
    }
    const lowTyres = Object.values(health?.tyres?.wheels ?? {}).filter((w) => w.state === 'low' || w.state === 'very_low').length;
    if (lowTyres) out.push({ text: `${lowTyres} tyre${lowTyres === 1 ? '' : 's'} low`, tone: 'warn', href: '#/health' });
    if (o.fuel_percent != null && o.fuel_percent <= 25) out.push({ text: `Fuel ${num(o.fuel_percent)}% · find the cheapest`, tone: 'warn', href: '#/fuel' });
    if (o.doors && !locked && !o.is_moving) out.push({ text: 'Not locked', tone: 'warn', href: '#/' });
    // Items due soon in the same month share one chip ("Engine oil and vehicle check due Jan 2027").
    const soon = new Map();
    for (const s of health?.services ?? []) {
      if (s.state !== 'soon') continue;
      const month = s.due ? new Date(s.due).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) : 'soon';
      soon.set(month, [...(soon.get(month) ?? []), s.name]);
    }
    for (const [month, names] of soon) {
      const list = names.map((n, i) => (i ? n.toLowerCase() : n)).join(' and ');
      out.push({ text: `${list} due ${month}`, tone: '', href: '#/health' });
    }
    return out;
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
  const weekAvg = $derived(weeks.length ? weeks.reduce((a, w) => a + w.km, 0) / weeks.length : null);
  const busiestWeek = $derived(weeks.length ? Math.max(...weeks.map((w) => w.km)) : null);
  const serviceLine = $derived(
    nextService
      ? `${nextService.name} ${new Date(nextService.due).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' })}`
      : null
  );

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
    <div class="head-title">
      <h1>{headline}</h1>
      <span class="small muted">updated {ago(o.updated_at)}</span>
    </div>
    <!-- The phone-glance line: what needs doing, or that nothing does. -->
    <ul class="attention" aria-label="Needs attention">
      {#each attention as a}
        <li><a class="pill {a.tone}" href={a.href}><span class="dot" style:background="currentColor" aria-hidden="true"></span>{a.text}</a></li>
      {:else}
        {#if health}<li><span class="pill ok"><span class="dot" style:background="currentColor" aria-hidden="true"></span>Nothing needs attention</span></li>{/if}
      {/each}
      {#if locked}<li><span class="pill ok"><span class="dot" style:background="currentColor" aria-hidden="true"></span>All doors secured</span></li>{/if}
    </ul>
  </header>

  <section class="bay" aria-label="Your car">
    <div class="stage">
      <div class="plate">
        <span class="plate-name">{name}</span>
        {#if vehicle.colour}<span class="small muted">{vehicle.colour}</span>{/if}
      </div>
      {#if vehicle.has_image && imageOk}
        <img src={api.imageUrl(vehicle.vin)} alt={`${name}${vehicle.colour ? ' in ' + vehicle.colour : ''}`} onerror={() => (imageOk = false)} />
      {/if}
    </div>

    <div class="strip">
      <div class="readout fuel">
        <span class="label">Fuel</span>
        <span class="value" style:color="var(--fuel)">{o.fuel_percent != null ? `${num(o.fuel_percent)}%` : DASH}{#if o.fuel_litres}<span class="unit">{num(o.fuel_litres.value, 1)} L</span>{/if}</span>
        <div class="gauge" role="img" aria-label={o.fuel_percent != null ? `Fuel ${num(o.fuel_percent)} percent` : 'Fuel level not reported yet'}>
          <div class="fill" style:width={`${o.fuel_percent ?? 0}%`} style:background="var(--fuel)"></div>
          <span class="tick" style:left="25%"></span><span class="tick" style:left="50%"></span><span class="tick" style:left="75%"></span>
        </div>
      </div>
      <div class="readout">
        <span class="label">Range</span>
        <span class="value">{o.range ? dist(o.range.value, prefs.distance) : DASH}<span class="unit">{distUnit(prefs.distance)}</span></span>
      </div>
      <div class="readout">
        <span class="label">Odometer</span>
        <span class="value">{o.odometer ? dist(o.odometer.value, prefs.distance) : DASH}<span class="unit">{distUnit(prefs.distance)}</span></span>
      </div>
      <div class="readout">
        <span class="label">This week</span>
        <span class="value">{thisWeek != null ? dist(thisWeek, prefs.distance) : DASH}<span class="unit">{distUnit(prefs.distance)}</span></span>
      </div>
    </div>
  </section>

  <section class="pair">
    <div class="card">
      <div class="card-head"><h2>Car status</h2><span class="small muted">{o.is_moving ? 'moving' : 'parked'}</span></div>
      <ul class="kv">
        {#each statusItems as s}
          <li>
            <span class="dot" style:background={s.value == null ? 'var(--idle)' : s.good ? 'var(--ok)' : 'var(--warn)'} aria-hidden="true"></span>
            <span class="k">{s.label}</span>
            <span class="v" class:quiet={s.value == null}>{s.value == null ? 'Not reported yet' : s.raw ? s.value : title(s.value)}</span>
          </li>
        {/each}
        <li>
          <span class="dot" style:background={!health ? 'var(--idle)' : health.issues ? 'var(--warn)' : 'var(--ok)'} aria-hidden="true"></span>
          <a class="k health-link" href="#/health">Health</a>
          <span class="v" style:color={health?.issues ? 'var(--warn)' : undefined}>
            {healthText ?? DASH}{#if serviceLine}<span class="small muted sub">{serviceLine}</span>{/if}
          </span>
        </li>
      </ul>
    </div>

    <div class="card location">
      <div class="card-head">
        <h2>{here ? `At ${here.name}` : 'Where it is'}</h2>
        {#if o.location?.heading != null}<span class="small muted">heading {num(o.location.heading)}°</span>{/if}
      </div>
      {#if o.location}
        <MapView
          markers={[{ lat: o.location.lat, lon: o.location.lon, colour: 'accent', radius: 9, label: here?.name ?? 'Your car' }]}
          height={262}
          label="Car location"
        />
      {:else}
        <p class="muted">No GPS position yet. It normally arrives after a drive.{o.heading != null ? ` Last heading ${num(o.heading)}°.` : ''}</p>
      {/if}
    </div>
  </section>

  <section class="card weekly">
    <div class="card-head"><h2>Distance per week</h2><span class="small muted">{distUnit(prefs.distance)}, last {weeks.length || 8} weeks</span></div>
    {#if weeks.length}
      <div class="weekly-body">
        <div class="weekly-facts">
          <div class="readout"><span class="label">This week</span><span class="value">{dist(thisWeek, prefs.distance)}</span></div>
          <div class="readout"><span class="label">Weekly average</span><span class="value">{dist(weekAvg, prefs.distance)}</span></div>
          <div class="readout"><span class="label">Busiest week</span><span class="value">{dist(busiestWeek, prefs.distance)}</span></div>
        </div>
        <div class="chart">
          <div class="bars" role="list" aria-label="Distance per week">
            {#each weeks as w, i}
              <div class="bar-col" role="listitem">
                <span class="sr-only">Week of {shortDate(w.week_start)}: {dist(w.km, prefs.distance)} {distUnit(prefs.distance)}</span>
                <span class="bar-v" aria-hidden="true">{dist(w.km, prefs.distance)}</span>
                <div class="bar" class:now={i === weeks.length - 1} aria-hidden="true" style:height={`${Math.max(2, (w.km / maxKm) * 110)}px`}></div>
              </div>
            {/each}
          </div>
          <div class="axis" class:sparse={weeks.length > 6} aria-hidden="true">
            {#each weeks as w}<span>{shortDate(w.week_start)}</span>{/each}
          </div>
        </div>
      </div>
    {:else}
      <p class="muted">Builds up as odometer readings arrive.</p>
    {/if}
  </section>
{:else}
  <Skeleton label="Loading the car" />
{/if}

<style>
  header { display: flex; flex-direction: column; gap: 12px; }
  .head-title { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; }
  .attention { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
  .attention a.pill { min-height: 32px; }
  .attention a.pill:hover { filter: brightness(1.2); }

  /* The garage bay: the car under one light, with its instrument strip beneath. */
  .bay { border: 1px solid var(--line-2); border-radius: 12px; overflow: hidden; background: var(--card); }
  .stage {
    position: relative;
    height: clamp(200px, 28vw, 300px);
    background: var(--bay);
    border-bottom: 1px solid var(--line-2);
  }
  .stage img { position: absolute; left: 50%; bottom: 4%; transform: translateX(-50%); width: min(92%, 760px); height: auto; max-height: 88%; object-fit: contain; }
  .plate { position: absolute; left: 20px; top: 16px; display: flex; flex-direction: column; gap: 2px; z-index: 1; }
  .strip .unit { margin-left: 4px; }
  .plate-name { font-family: var(--display); font-weight: 600; font-size: 17px; }

  .bay .strip { border-top: 0; }
  .fuel .gauge { margin-top: 6px; }

  .pair { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); align-items: stretch; }
  .location { display: flex; flex-direction: column; }
  .location p { margin: 0; }
  .kv .v { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
  .kv .v.quiet { color: var(--muted); font-weight: 400; }
  .health-link { color: var(--muted); padding: 13px 0; margin: -13px 0; }
  .health-link:hover { color: var(--accent); }
  .health-link::after { content: ' →'; }
  .sub { font-weight: 400; }

  .weekly-body { display: grid; grid-template-columns: 180px 1fr; gap: 24px; align-items: end; }
  .weekly-facts { display: flex; flex-direction: column; gap: 16px; padding-right: 24px; border-right: 1px solid var(--line); }
  .chart { min-width: 0; }

  @media (max-width: 760px) {
    .weekly-body { grid-template-columns: 1fr; }
    .weekly-facts { flex-direction: row; justify-content: space-between; padding: 0 0 16px; border-right: 0; border-bottom: 1px solid var(--line); }
    .axis.sparse > span:nth-child(odd) { visibility: hidden; }
    .stage { height: 200px; }
  }
</style>
