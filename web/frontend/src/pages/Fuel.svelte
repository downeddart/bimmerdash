<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num, money, longDate, shortDate, monthName, ago, economy, economyUnit, DASH, toUnit, distUnit, perDistance } from '../lib/format.js';
  import { prefs, setEconomyUnit } from '../lib/prefs.svelte.js';
  import MapView from '../components/MapView.svelte';

  let { vehicle } = $props();
  let data = $state(null);
  let nearby = $state(null);
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
        colour: f.price_source === 'uk_average' ? '#f2b33d' : '#3dd68c',
        label: `${shortDate(f.time)} · ${f.station?.trading_name ?? 'Fill-up'}`,
      }))
  );
  const stationMarkers = $derived(
    stations.map((x, i) => ({
      lat: x.lat,
      lon: x.lon,
      colour: i === 0 ? '#3dd68c' : '#5aa2ff',
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
  const SOURCE = { station: 'station price', receipt: 'receipt', uk_average: 'UK avg' };

  onMount(() => {
    api.fuel(vehicle.vin).then((d) => (data = d)).catch((e) => (error = e.message));
    api.nearby(vehicle.vin).then((d) => (nearby = d)).catch(() => (nearby = { stations: [] }));
  });
</script>

<header>
  <div>
    <div class="eyebrow plain">Detected from fuel level jumps</div>
    <h1>Fuel</h1>
  </div>
  <div class="seg" role="group" aria-label="Economy units">
    <button class:on={prefs.economy === 'l100'} aria-pressed={prefs.economy === 'l100'} onclick={() => setEconomyUnit('l100')}>L/100 km</button>
    <button class:on={prefs.economy === 'mpg'} aria-pressed={prefs.economy === 'mpg'} onclick={() => setEconomyUnit('mpg')}>mpg (UK)</button>
  </div>
</header>

{#if error}
  <div class="card">Could not load fuel data: {error}</div>
{:else if !data}
  <div class="muted">Loading…</div>
{:else}
  <div class="grid" style="--min: 150px">
    <div class="card tile">
      <span class="muted">Average economy</span>
      <span class="num tile-v">{economy(s?.economy_l100, prefs.economy)} <span class="unit">{economyUnit(prefs.economy)}</span></span>
      <span class="small muted">{s?.economy_intervals ? `last ${s.economy_intervals} fill-up${s.economy_intervals > 1 ? 's' : ''}` : 'after your second fill-up'}</span>
    </div>
    <div class="card tile">
      <span class="muted">Spent this month</span>
      <span class="num tile-v">{money(s?.month_cost ?? 0)}</span>
      <span class="small muted">{s?.month_fillups ?? 0} fill-up{s?.month_fillups === 1 ? '' : 's'}</span>
    </div>
    <div class="card tile">
      <span class="muted">Spent {s?.since ? `since ${longDate(s.since)}` : 'in total'}</span>
      <span class="num tile-v">{money(s?.total_cost ?? 0)}</span>
      {#if s?.total_estimated}
        <span class="small est">~{money(s.total_estimated)} of it estimated from UK averages</span>
      {:else}
        <span class="small muted">{fills.length} fill-up{fills.length === 1 ? '' : 's'}</span>
      {/if}
    </div>
    <div class="card tile">
      <span class="muted">Fuel cost per {prefs.distance === 'mi' ? 'mile' : 'km'}</span>
      <span class="num tile-v">{s?.pence_per_km != null ? `${num(perDistance(s.pence_per_km, prefs.distance), 1)}p` : DASH}</span>
      <span class="small muted">{s?.avg_price_ppl ? `at ${num(s.avg_price_ppl, 1)}p/L recently` : 'needs economy and prices'}</span>
    </div>
  </div>

  <div class="two">
    <div class="card">
      <div class="card-head"><h2>Monthly spend</h2><span class="small muted">fuel only</span></div>
      {#if months.length}
        <div class="bars" role="img" aria-label="Fuel spend per month">
          {#each months as m}
            <div class="bar-col">
              <span class="small muted">{money(m.cost)}</span>
              <div class="bar" style:height={`${Math.max(3, (m.cost / maxMonth) * 120)}px`}>
                {#if m.estimated}<div class="bar-est" style:height={`${(m.estimated / m.cost) * 100}%`}></div>{/if}
              </div>
              <span class="small muted">{monthName(m.month)}</span>
            </div>
          {/each}
        </div>
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
          {#if fillMarkers.length}<span><span class="sw" style="background:#3dd68c"></span>fill-up</span>{/if}
          {#if stationMarkers.length}<span><span class="sw" style="background:#5aa2ff"></span>nearby price</span>{/if}
        </span>
      </div>
      <MapView markers={[...stationMarkers, ...fillMarkers]} height={320} label="Fill-ups and nearby stations" />
    </div>
  {/if}

  <div class="card">
    <div class="card-head"><h2>Fill-ups</h2><span class="small muted">{fills.length}</span></div>
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
                        <span class="tag">UK avg</span> {num(f.price_ppl, 1)}p/L: UK weekly average (DESNZ), not this station's price
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
  header { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
  .seg { display: inline-flex; padding: 3px; border-radius: 999px; background: var(--card); border: 1px solid var(--line); }
  .seg button { border: 0; background: none; padding: 8px 14px; border-radius: 999px; cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 500; min-height: 36px; }
  .seg button.on { background: var(--card-3); color: var(--text); }
  .tile { display: flex; flex-direction: column; gap: 6px; padding: 20px; }
  .tile-v { font-size: 26px; }
  .est { color: var(--warn); }
  .est-bg { background: var(--warn); }
  .tag { display: inline-block; padding: 1px 6px; border-radius: 6px; background: #2a2212; color: var(--warn); font-size: 11px; font-weight: 600; }
  .two { display: grid; gap: 16px; align-items: start; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); }
  .card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
  .bars { display: flex; align-items: flex-end; gap: 10px; height: 180px; }
  .bar-col { flex: 1 1 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 8px; height: 100%; min-width: 0; }
  .bar { width: 100%; max-width: 40px; border-radius: 6px 6px 3px 3px; background: var(--accent); display: flex; flex-direction: column-reverse; overflow: hidden; }
  .bar-est { width: 100%; background: var(--warn); }
  .legend, .legend-inline { display: flex; gap: 14px; align-items: center; margin-top: 12px; }
  .legend-inline { margin: 0; }
  .legend-inline > span { display: inline-flex; gap: 6px; align-items: center; }
  .sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
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
  code { font-size: 12px; background: var(--card-3); padding: 1px 5px; border-radius: 5px; }
</style>
