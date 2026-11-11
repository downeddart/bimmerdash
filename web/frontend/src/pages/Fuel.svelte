<script>
  import Skeleton from '../components/Skeleton.svelte';
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num, money, longDate, shortDate, monthName, ago, economy, economyUnit, DASH, toUnit, distUnit, perDistance } from '../lib/format.js';
  import { prefs, setEconomyUnit } from '../lib/prefs.svelte.js';
  import MapView from '../components/MapView.svelte';

  let { vehicle } = $props();
  let data = $state(null);
  let nearby = $state(null);
  let advice = $state(null);
  let error = $state(null);

  const s = $derived(data?.summary);
  const fills = $derived(data?.fillups ?? []);
  const months = $derived(data?.monthly ?? []);
  const maxMonth = $derived(Math.max(1, ...months.map((m) => m.cost)));
  const stations = $derived(nearby?.stations ?? []);
  const spread = $derived(
    stations.length > 1 ? Math.max(...stations.map((x) => x.price)) - Math.min(...stations.map((x) => x.price)) : 0
  );
  const fillMarkers = $derived(
    fills
      .filter((f) => f.lat != null)
      .map((f) => ({
        lat: f.lat,
        lon: f.lon,
        colour: f.price_source === 'uk_average' ? 'warn' : 'ok',
        label: `${shortDate(f.time)} · ${f.station?.trading_name ?? 'Fill-up'}`,
      }))
  );
  const stationMarkers = $derived(
    stations.map((x, i) => ({
      lat: x.lat,
      lon: x.lon,
      colour: i === 0 ? 'ok' : 'accent',
      radius: 6,
      label: `${x.name} · ${num(x.price, 1)}p`,
    }))
  );
  const gradeName = $derived(
    { E10: 'E10 petrol', E5: 'E5 super unleaded', B7_STANDARD: 'Diesel', B7_PREMIUM: 'Premium diesel' }[
      nearby?.grade ?? data?.grade
    ] ?? (nearby?.grade ?? 'Fuel')
  );

  const isAvg = (f) => f.price_source === 'uk_average';
  const ce = $derived(data?.car_economy);
  // Best economy figure: the car's own counters, else fill-to-fill.
  const econ = $derived(
    ce?.recent_l100
      ? { l100: ce.recent_l100, note: `car's own figure, last ${num(toUnit(ce.recent_km, prefs.distance))} ${distUnit(prefs.distance)}` }
      : s?.economy_l100
        ? { l100: s.economy_l100, note: `last ${s.economy_intervals} fill-up${s.economy_intervals > 1 ? 's' : ''}` }
        : ce?.lifetime_l100
          ? { l100: ce.lifetime_l100, note: `car's lifetime figure, ${num(toUnit(ce.lifetime_km, prefs.distance))} ${distUnit(prefs.distance)}` }
          : null
  );
  const adviceMarkers = $derived(
    !advice?.position
      ? []
      : [
          ...advice.stations.map((x) => ({
            lat: x.lat,
            lon: x.lon,
            colour: x === advice.stations[0] ? 'ok' : 'accent',
            radius: x === advice.stations[0] ? 8 : 6,
            label: `${x.name} · ${num(x.price, 1)}p`,
          })),
          { lat: advice.position.lat, lon: advice.position.lon, colour: 'text', radius: 7, label: 'Your car' },
        ]
  );
  const ECON_SOURCE = { car_recent: "the car's recent economy", car_lifetime: "the car's lifetime economy", fillups: 'your fill-ups', assumed: 'a typical figure until the car reports its own' };
  const SOURCE = { station: 'station price', receipt: 'receipt', uk_average: 'UK avg' };

  onMount(() => {
    api.fuel(vehicle.vin).then((d) => (data = d)).catch((e) => (error = e.message));
    api.nearby(vehicle.vin).then((d) => (nearby = d)).catch(() => (nearby = { stations: [] }));
    api.advisor(vehicle.vin).then((d) => (advice = d)).catch(() => (advice = null));
  });
</script>

<header>
  <h1>Fuel</h1>
  <div class="seg" role="group" aria-label="Economy units">
    <button class:on={prefs.economy === 'l100'} aria-pressed={prefs.economy === 'l100'} onclick={() => setEconomyUnit('l100')}>L/100 km</button>
    <button class:on={prefs.economy === 'mpg'} aria-pressed={prefs.economy === 'mpg'} onclick={() => setEconomyUnit('mpg')}>mpg (UK)</button>
  </div>
</header>

{#if error}
  <div class="card">Could not load fuel data: {error}</div>
{:else if !data}
  <Skeleton label="Loading fuel" />
{:else}
  {#if advice?.best}
    {@const b = advice.best}
    {@const n = advice.nearest}
    <section class="card advisor">
      <div class="adv-text">
        <div class="card-head"><h2>Where to fill up next</h2><span class="small muted">within {num(toUnit(advice.radius_km, prefs.distance))} {distUnit(prefs.distance)}</span></div>
        <div class="adv-title">{b.name}</div>
        <div class="muted">
          {num(b.price, 1)}p/L · {num(toUnit(b.distance_km, prefs.distance), 1)} {distUnit(prefs.distance)} from the car{b.brand && b.brand !== b.name ? ` · ${b.brand}` : ''}
        </div>
        <div class="strip inset adv-facts" style="--cols: 3">
          <div class="readout">
            <span class="label">{advice.fill_litres ? `Filling ${num(advice.fill_litres)} L` : `A ${num(advice.litres_compared)} L top-up`}</span>
            <span class="value">{money(b.fill_cost)}</span>
          </div>
          <div class="readout">
            <span class="label">Saving vs nearest</span>
            <span class="value" class:good={b.saving > 0.05}>{b.node_id === n.node_id ? 'Nearest' : money(b.saving)}</span>
          </div>
          <div class="readout">
            <span class="label">In the tank</span>
            <span class="value">{advice.fuel.percent != null ? `${num(advice.fuel.percent)}%` : DASH}</span>
          </div>
        </div>
        <p class="small muted">
          {#if b.node_id !== n.node_id}Nearest is {n.name} at {num(n.price, 1)}p, {num(toUnit(n.distance_km, prefs.distance), 1)} {distUnit(prefs.distance)} away. {/if}
          Counts the fuel to drive there and back ({money(b.detour_cost)}), using {ECON_SOURCE[advice.economy.source]}.
        </p>
      </div>
      <div class="adv-map"><MapView markers={adviceMarkers} height={260} label="Stations near the car" /></div>
    </section>
  {/if}

  <section class="strip boxed" aria-label="Fuel summary">
    <div class="readout">
      <span class="label">Economy</span>
      <span class="value">{economy(econ?.l100, prefs.economy)}<span class="unit">{economyUnit(prefs.economy)}</span></span>
      <span class="note">{econ?.note ?? 'after the car reports its fuel use, or your second fill-up'}</span>
    </div>
    <div class="readout">
      <span class="label">Spent this month</span>
      <span class="value">{money(s?.month_cost ?? 0)}</span>
      <span class="note">{s?.month_fillups ?? 0} fill-up{s?.month_fillups === 1 ? '' : 's'}</span>
    </div>
    <div class="readout">
      <span class="label">Spent {s?.since ? `since ${longDate(s.since)}` : 'in total'}</span>
      <span class="value">{money(s?.total_cost ?? 0)}</span>
      {#if s?.total_estimated}
        <span class="note est">~{money(s.total_estimated)} of it estimated from UK averages</span>
      {:else}
        <span class="note">{fills.length} fill-up{fills.length === 1 ? '' : 's'}</span>
      {/if}
    </div>
    <div class="readout">
      <span class="label">Fuel cost per {prefs.distance === 'mi' ? 'mile' : 'km'}</span>
      <span class="value">{s?.pence_per_km != null ? `${num(perDistance(s.pence_per_km, prefs.distance), 1)}p` : DASH}</span>
      <span class="note">{s?.avg_price_ppl ? `at ${num(s.avg_price_ppl, 1)}p/L ${s.price_basis === 'local' ? 'near home' : s.price_basis === 'uk_average' ? '(UK average)' : 'recently'}` : 'needs economy and prices'}</span>
    </div>
  </section>

  <div class="two">
    <div class="card">
      <div class="card-head"><h2>Monthly spend</h2><span class="small muted">fuel only</span></div>
      {#if months.length}
        <div class="bars" role="list" aria-label="Fuel spend per month">
          {#each months as m}
            <div class="bar-col" role="listitem">
              <span class="sr-only">{monthName(m.month)}: {money(m.cost)}{m.estimated ? `, of which ${money(m.estimated)} estimated from UK averages` : ''}</span>
              <span class="bar-v" aria-hidden="true">{money(m.cost)}</span>
              <div class="bar spend" aria-hidden="true" style:height={`${Math.max(3, (m.cost / maxMonth) * 110)}px`}>
                {#if m.estimated}<div class="bar-est" style:height={`${(m.estimated / m.cost) * 100}%`}></div>{/if}
              </div>
            </div>
          {/each}
        </div>
        <div class="axis" aria-hidden="true">{#each months as m}<span>{monthName(m.month)}</span>{/each}</div>
        {#if months.some((m) => m.estimated)}
          <div class="legend small muted"><span class="sw est-bg"></span>estimated from UK weekly averages</div>
        {/if}
      {:else}
        <p class="muted">Spend per month appears after your first detected fill-up.</p>
      {/if}
    </div>

    <div class="card">
      <div class="card-head">
        <h2>{gradeName} near {nearby?.centre?.source === 'home' ? 'home' : 'the car'}</h2>
        <span class="small muted">UK Fuel Finder</span>
      </div>
      {#if !nearby}
        <div class="muted">Loading…</div>
      {:else if !nearby.centre}
        <p class="muted">Needs the car's position first.</p>
      {:else if !stations.length}
        <p class="muted">No recent prices within {num(toUnit(nearby.radius_km, prefs.distance))} {distUnit(prefs.distance)}. Station prices need Fuel Finder credentials in <code>.env</code>.</p>
      {:else}
        <ul class="stations">
          {#each stations as x, i}
            <li>
              <div>
                <div class="st-name">{x.name}</div>
                <div class="small muted">{num(toUnit(x.distance_km, prefs.distance), 1)} {distUnit(prefs.distance)} away · updated {ago(x.updated_at)}</div>
              </div>
              <span class="num price" class:best={i === 0}>{num(x.price, 1)}p</span>
            </li>
          {/each}
        </ul>
        {#if spread >= 1}
          <p class="small muted">50 litres at the cheapest costs {money((spread * 50) / 100)} less than at the dearest here.</p>
        {/if}
      {/if}
    </div>
  </div>

  {#if fillMarkers.length || stationMarkers.length}
    <div class="card">
      <div class="card-head">
        <h2>Map</h2>
        <span class="small muted legend-inline">
          {#if fillMarkers.length}<span><span class="sw" style="background: var(--ok)"></span>fill-up</span>{/if}
          {#if stationMarkers.length}<span><span class="sw" style="background: var(--accent)"></span>nearby price</span>{/if}
        </span>
      </div>
      <MapView markers={[...stationMarkers, ...fillMarkers]} height={320} label="Fill-ups and nearby stations" />
    </div>
  {/if}

  <div class="card">
    <div class="card-head"><h2>Fill-ups</h2><span class="small muted">{fills.length} detected from fuel level jumps</span></div>
    {#if !fills.length}
      <p class="muted">No fill-ups detected yet. After you next fill up, it appears here once the car reports its new fuel level, priced at that station's price where it can be matched.</p>
    {:else}
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Date</th><th>Station</th><th class="r">Added</th><th class="r">Cost</th><th>Tank</th><th class="r">Since last</th><th class="r">Economy</th></tr>
          </thead>
          <tbody>
            {#each fills as f}
              <tr>
                <td>{longDate(f.time)}</td>
                <td>
                  <div>{f.station?.trading_name ?? 'Station not matched'}</div>
                  {#if f.price_ppl != null}
                    {#if isAvg(f)}
                      <div class="small est" title="UK weekly average (DESNZ), not this station's price">
                        <span class="tag est">UK avg</span> {num(f.price_ppl, 1)}p/L: UK weekly average (DESNZ), not this station's price
                      </div>
                    {:else}
                      <div class="small muted">{num(f.price_ppl, 1)}p/L · {SOURCE[f.price_source]}</div>
                    {/if}
                  {/if}
                </td>
                <td class="r">{f.litres_added != null ? `${f.litres_estimated ? '≈' : ''}${num(f.litres_added, 1)} L` : DASH}</td>
                <td class="r num" class:est={isAvg(f)}>{f.cost_gbp != null ? `${isAvg(f) ? '~' : ''}${money(f.cost_gbp)}` : DASH}</td>
                <td class="small">{f.percent_before != null ? `${num(f.percent_before)}% → ${num(f.percent_after)}%` : DASH}</td>
                <td class="r">{f.km_since_last != null ? `${num(toUnit(f.km_since_last, prefs.distance))} ${distUnit(prefs.distance)}` : DASH}</td>
                <td class="r">{f.economy_l100 != null ? `${economy(f.economy_l100, prefs.economy)}` : DASH}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="small muted">Litres added are from the car's fuel sensor, so they can differ from the receipt by a litre or two.</p>
    {/if}
  </div>
{/if}

<style>
  header { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px 16px; flex-wrap: wrap; }
  .advisor { display: grid; gap: 20px; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
  @media (max-width: 900px) { .advisor { grid-template-columns: 1fr; } }
  .adv-text { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .adv-text .card-head { margin-bottom: 8px; }
  .adv-title { font-family: var(--display); font-weight: 600; font-size: 26px; }
  .adv-facts { margin: 12px 0 4px; }
  .adv-facts .value { font-size: 20px; }
  .good { color: var(--ok); }
  .adv-text p { margin: 4px 0 0; }
  .est { color: var(--warn); }
  .est-bg { background: var(--warn); }
  .two { display: grid; gap: 16px; align-items: start; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); }
  .bar.spend { background: var(--accent); display: flex; flex-direction: column-reverse; overflow: hidden; }
  .bar-est { width: 100%; background: var(--warn); }
  .legend, .legend-inline { display: flex; gap: 14px; align-items: center; margin-top: 12px; }
  .legend-inline { margin: 0; }
  .legend-inline > span { display: inline-flex; gap: 6px; align-items: center; }
  .sw { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
  .stations { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .stations li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); }
  .stations li:first-child { border-top: 0; }
  .st-name { font-weight: 500; }
  .price { font-size: 18px; }
  .price.best { color: var(--ok); }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 720px; }
  th { text-align: left; font-size: 12px; color: var(--muted); font-weight: 500; padding: 0 10px 10px; }
  td { padding: 12px 10px; border-top: 1px solid var(--line); vertical-align: top; }
  .r { text-align: right; }
  code { font-size: 12px; background: var(--card-3); padding: 1px 5px; border-radius: 4px; }
</style>
