<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num, ago, longDate, dateTime, toUnit, distUnit, DASH } from '../lib/format.js';
  import { prefs } from '../lib/prefs.svelte.js';

  let { vehicle } = $props();
  let h = $state(null);
  let error = $state(null);
  let showCodes = $state(false);

  const PSI = 0.145038;
  const WHEELS = [
    ['front_left', 'Front left'],
    ['front_right', 'Front right'],
    ['rear_left', 'Rear left'],
    ['rear_right', 'Rear right'],
  ];
  const STATE = {
    ok: { label: 'OK', colour: 'var(--ok)' },
    soon: { label: 'Due soon', colour: 'var(--warn)' },
    overdue: { label: 'Overdue', colour: 'var(--bad)' },
    low: { label: 'Low', colour: 'var(--warn)' },
    very_low: { label: 'Very low', colour: 'var(--bad)' },
    unknown: { label: 'No target', colour: 'var(--muted)' },
  };
  const KIND = { left_open: 'Left open', low_fuel: 'Fuel', tyre: 'Tyres', service: 'Service', warning: 'Warning' };

  const tyres = $derived(h?.tyres);
  const warnings = $derived(h?.warnings?.items ?? []);
  const summary = $derived.by(() => {
    if (!h) return null;
    if (h.issues) return { text: `${h.issues} thing${h.issues === 1 ? '' : 's'} to look at`, cls: 'warn' };
    if (h.due_soon) return { text: `${h.due_soon} service item${h.due_soon === 1 ? '' : 's'} due soon`, cls: 'warn' };
    return { text: 'All good', cls: 'ok' };
  });

  function dueMonth(s) {
    return s.due ? new Date(s.due).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) : DASH;
  }
  function dueIn(s) {
    if (!s.due) return 'no date given';
    const days = Math.round((new Date(s.due) - Date.now()) / 86400000);
    if (days < 0) return `${-days} days overdue`;
    if (days < 60) return `in ${days} days`;
    return `in ${Math.round(days / 30.4)} months`;
  }

  const psi = (kpa) => (kpa == null ? DASH : num(kpa * PSI));

  onMount(async () => {
    try {
      h = await api.health(vehicle.vin);
    } catch (e) {
      error = e.message;
    }
  });
</script>

<header>
  <h1>Health</h1>
  {#if summary}<span class="pill {summary.cls}"><span class="dot" style:background="currentColor" aria-hidden="true"></span>{summary.text}</span>{/if}
</header>

{#if error}
  <div class="card">Could not load health data: {error}</div>
{:else if !h}
  <div class="muted">Loading…</div>
{:else}
  {#if !h.rest.last_poll}
    <div class="card note">
      <strong>Waiting for BMW.</strong>
      <span class="muted">Service dates, warnings and the car's own fuel figures come from BMW's data API, asked every 4 hours. The first answer appears here within a few minutes of the collector starting.</span>
    </div>
  {/if}

  <section class="two">
    <div class="card">
      <div class="card-head"><h2>Service schedule</h2><span class="small muted">from the car</span></div>
      {#if !h.services.length}
        <p class="muted">Not reported yet.</p>
      {:else}
        <table class="sched">
          <thead>
            <tr><th scope="col">Item</th><th scope="col">Due</th><th scope="col" class="r dist-col">Or in</th><th scope="col" class="r"><span class="sr-only">State</span></th></tr>
          </thead>
          <tbody>
            {#each h.services as s}
              <tr>
                <th scope="row"><span class="dot" style:background={STATE[s.state].colour} aria-hidden="true"></span>{s.name}</th>
                <td><span class="num">{dueMonth(s)}</span><span class="small muted">{dueIn(s)}{#if s.distance_km != null}<span class="dist-inline">{` · ${num(toUnit(s.distance_km, prefs.distance))} ${distUnit(prefs.distance)}`}</span>{/if}</span></td>
                <td class="r num dist-col">{s.distance_km != null ? `${num(toUnit(s.distance_km, prefs.distance))} ${distUnit(prefs.distance)}` : DASH}</td>
                <td class="r state" style:color={STATE[s.state].colour}>{STATE[s.state].label}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>

    <div class="card">
      <div class="card-head">
        <h2>Tyre pressures</h2>
        <span class="small muted">{tyres ? `psi, ${ago(tyres.time)}` : ''}</span>
      </div>
      {#if !tyres}
        <p class="muted">Not reported yet. The car sends pressures every so often while driving.</p>
      {:else}
        <div class="car" role="group" aria-label="Tyre pressures by wheel">
          {#each WHEELS as [pos, label]}
            {@const w = tyres.wheels[pos]}
            <div class="wheel" class:front={pos.startsWith('front')} style:--c={w ? STATE[w.state].colour : 'var(--idle)'}>
              <span class="small muted wheel-name"><span class="dot" style:background="var(--c)" aria-hidden="true"></span>{label}{#if w && w.state !== 'ok'}<span class="sr-only">, {STATE[w.state].label}</span>{/if}</span>
              <span class="num big">{w ? psi(w.kpa) : DASH}{#if w}<span class="sr-only"> psi</span>{/if}</span>
              <span class="small muted wheel-meta">
                {#if w?.target_kpa}<span>target {psi(w.target_kpa)}</span>{#if w.below_target_pct > 0}<span style:color="var(--c)">−{num(w.below_target_pct)}%</span>{/if}{/if}
                {#if w?.temp_c != null}<span>{num(w.temp_c)}°C</span>{/if}
              </span>
            </div>
          {/each}
          <svg class="outline" viewBox="0 0 60 140" aria-hidden="true" preserveAspectRatio="none">
            <rect x="8" y="4" width="44" height="132" rx="16" />
            <path d="M14 40 Q30 32 46 40" />
            <path d="M14 104 Q30 110 46 104" />
            <line x1="30" y1="4" x2="30" y2="10" />
          </svg>
        </div>
        {#if tyres.state === 'low' || tyres.state === 'very_low'}
          <p class="small warn-text">Below the pressure the car expects. Low tyres wear faster and use more fuel: check them when cold and pump them up to the placard pressure.</p>
        {:else}
          <p class="small muted">Targets are the car's own, for its current load setting.</p>
        {/if}
      {/if}
    </div>
  </section>

  <section class="two">
    <div class="card">
      <div class="card-head"><h2>Warning lights</h2><span class="small muted">Check Control</span></div>
      {#if !h.warnings}
        <p class="muted">Not reported yet.</p>
      {:else if !warnings.length}
        <ul class="kv"><li><span class="dot" style:background="var(--ok)" aria-hidden="true"></span><span class="k">No messages</span><span class="small muted">{ago(h.warnings.time)}</span></li></ul>
      {:else}
        <ul class="list">
          {#each warnings as w}
            <li><span class="dot" style:background="var(--warn)"></span><div class="grow name">{w.text}</div></li>
          {/each}
        </ul>
      {/if}

      {#if h.fault_memory}
        <div class="faults">
          <div>
            <div class="name">Fault memory</div>
            <div class="small muted">
              {h.fault_memory.count} stored code{h.fault_memory.count === 1 ? '' : 's'} in {h.fault_memory.modules} module{h.fault_memory.modules === 1 ? '' : 's'}, as of {longDate(h.fault_memory.time)}.
              Most are old or minor; a garage's scan tool reads them properly.
            </div>
          </div>
          <button class="btn ghost" onclick={() => (showCodes = !showCodes)} aria-expanded={showCodes}>{showCodes ? 'Hide' : 'Show'} codes</button>
        </div>
        {#if showCodes}<p class="codes small">{h.fault_memory.codes.join('  ')}</p>{/if}
      {/if}
    </div>

    <div class="card">
      <div class="card-head">
        <h2>Alerts</h2>
        <a class="small" href="#/settings">Alert settings</a>
      </div>
      {#if h.alerts.pushover === false}
        <p class="small muted">Shown here only. Add Pushover keys to <code>.env</code> to get them on your phone.</p>
      {:else if h.alerts.pushover}
        <p class="small muted">New alerts are sent to your phone with Pushover.</p>
      {/if}
      {#if !h.alerts.recent.length}
        <p class="muted">Nothing yet.</p>
      {:else}
        <ul class="list">
          {#each h.alerts.recent as a}
            <li class:cleared={a.cleared_at}>
              <span class="dot" style:background={a.cleared_at ? 'var(--idle)' : 'var(--warn)'}></span>
              <div class="grow">
                <div class="name">{a.title}</div>
                <div class="small muted">{a.message}</div>
                <div class="small muted">{KIND[a.kind] ?? a.kind} · {dateTime(a.raised_at)}{a.cleared_at ? ' · resolved' : ''}{a.send_error ? ` · not sent (${a.send_error})` : ''}</div>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </section>

  <p class="small muted">Last asked BMW {ago(h.rest.last_poll)}.</p>
{/if}

<style>
  header { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px 16px; flex-wrap: wrap; }
  .note { display: flex; flex-direction: column; gap: 6px; }
  .two { display: grid; gap: 16px; align-items: start; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); }

  .sched { width: 100%; border-collapse: collapse; }
  .sched th, .sched td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--line); vertical-align: top; font-weight: 400; }
  .sched thead th { border-top: 0; padding-top: 0; font-size: 12px; color: var(--muted); font-weight: 500; }
  .sched tbody th { font-weight: 500; white-space: nowrap; }
  .sched tbody th .dot { margin-right: 10px; vertical-align: 2px; }
  .sched th:first-child, .sched td:first-child { padding-left: 0; }
  .sched th:last-child, .sched td:last-child { padding-right: 0; }
  .sched td .num { display: block; white-space: nowrap; }
  .dist-inline { display: none; }
  .sched .r { text-align: right; }
  .state { font-weight: 600; font-size: 12px; white-space: nowrap; }

  .list { list-style: none; margin: 0; padding: 0; }
  .list li { display: flex; align-items: flex-start; gap: 12px; padding: 12px 0; border-top: 1px solid var(--line); }
  .list li:first-child { border-top: 0; padding-top: 4px; }
  .list .dot { margin-top: 6px; }
  .list li.cleared .name { color: var(--muted); font-weight: 400; }
  .grow { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .name { font-weight: 500; }

  /* Tyres: four readouts around a drawn outline of the car, nose up. */
  .car { position: relative; display: grid; grid-template-columns: 1fr 1fr; gap: 12px 76px; }
  .outline { position: absolute; left: 50%; top: 6px; width: 52px; height: calc(100% - 12px); transform: translateX(-50%); fill: none; stroke: var(--line-2); stroke-width: 1.5; overflow: visible; }
  .outline * { vector-effect: non-scaling-stroke; }
  .wheel { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border-radius: 8px; background: var(--card-2); border: 1px solid var(--line); }
  .wheel-name { display: flex; align-items: center; gap: 8px; }
  .wheel-meta { display: flex; flex-wrap: wrap; gap: 0 10px; }
  .big { font-size: 28px; color: var(--c); }
  .warn-text { color: var(--warn); }

  .faults { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
  .codes { font-family: ui-monospace, monospace; word-break: break-word; color: var(--muted); }
  code { font-size: 12px; background: var(--card-3); padding: 1px 5px; border-radius: 4px; }
  @media (max-width: 480px) {
    .car { gap: 10px 36px; }
    .outline { width: 30px; }
    .sched thead { display: none; }
    .sched tbody th { white-space: normal; }
    .dist-col { display: none; }
    .dist-inline { display: inline; }
  }
</style>
