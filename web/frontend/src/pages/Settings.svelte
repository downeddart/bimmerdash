<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { num } from '../lib/format.js';
  import { prefs, loadSettings, saveGlobal } from '../lib/prefs.svelte.js';

  let { vehicle, account = null, onSignedOut } = $props();
  let pwCurrent = $state('');
  let pwNew = $state('');
  let pwRepeat = $state('');
  let pwMsg = $state(null);
  let pwErr = $state(null);

  async function changePassword(e) {
    e.preventDefault();
    pwMsg = pwErr = null;
    if (pwNew.length < 10) return (pwErr = 'Use at least 10 characters.');
    if (pwNew !== pwRepeat) return (pwErr = "New passwords don't match.");
    try {
      await api.changePassword(pwCurrent, pwNew);
      pwCurrent = pwNew = pwRepeat = '';
      pwMsg = 'Password changed. Other devices have been signed out.';
    } catch (err) {
      pwErr = err.message;
    }
  }

  async function signOut() {
    try {
      await api.logout();
    } finally {
      onSignedOut?.();
    }
  }
  let all = $state(null);
  let error = $state(null);
  let saved = $state(null); // which section last saved, for the confirmation
  let tankInput = $state('');
  let limitInput = $state('');
  let limitStart = $state('');
  let pushover = $state(null);
  const KM_PER_MI = 1.609344;
  const al = $derived(all?.global?.alerts);
  const ALERTS = [
    ['left_open', 'Left unlocked or open', 'Doors, windows, boot or bonnet, once the car has been parked a while'],
    ['low_fuel', 'Fuel low', 'With the cheapest station near the car'],
    ['tyres', 'Tyre pressure low', "Below the car's own target pressure"],
    ['service', 'Service or MOT due', 'Shortly before the due date'],
    ['warnings', 'Warning lights', 'Any Check Control message from the car'],
  ];

  function setAlerts(patch) {
    setGlobal({ alerts: patch }, 'alerts');
  }

  function saveLimit() {
    const v = limitInput === '' ? 0 : Number(limitInput);
    if (Number.isNaN(v) || v < 0 || v > 100000) {
      error = 'Mileage limit must be between 0 and 100,000.';
      return;
    }
    const km = prefs.distance === 'mi' ? v * KM_PER_MI : v;
    setVehicle({ mileage_limit_km: Math.round(km), mileage_year_start: limitStart || '' });
  }

  const car = $derived(vehicle ? all?.vehicles?.[vehicle.vin] : null);
  const carName = $derived(vehicle ? [vehicle.model_name, vehicle.body_type].filter(Boolean).join(' ') || 'Your car' : '');
  const defaultGrade = $derived(car?.fuel_kind === 'diesel' ? 'B7_STANDARD' : 'E10');

  // Vehicles can load after this page mounts.
  $effect(() => {
    if (car?.tank_litres != null && tankInput === '') tankInput = car.tank_litres;
  });
  $effect(() => {
    if (car?.mileage_limit_km && limitInput === '') {
      limitInput = Math.round(prefs.distance === 'mi' ? car.mileage_limit_km / KM_PER_MI : car.mileage_limit_km);
    }
    if (car?.mileage_year_start && limitStart === '') limitStart = car.mileage_year_start;
  });

  function flash(section) {
    saved = section;
    setTimeout(() => saved === section && (saved = null), 4000);
  }

  async function setGlobal(patch, section) {
    error = null;
    try {
      all = await saveGlobal(patch);
      flash(section);
    } catch (e) {
      error = e.message;
    }
  }

  async function setVehicle(patch) {
    error = null;
    try {
      all = await api.putVehicleSettings(vehicle.vin, patch);
      flash('car');
    } catch (e) {
      error = e.message;
    }
  }

  function saveTank() {
    const v = tankInput === '' ? 0 : Number(tankInput);
    if (Number.isNaN(v) || v < 0 || v > 200) {
      error = 'Tank size must be between 0 and 200 litres.';
      return;
    }
    setVehicle({ tank_litres: v });
  }

  onMount(async () => {
    try {
      all = await loadSettings();
      tankInput = all.vehicles?.[vehicle?.vin]?.tank_litres ?? '';
      if (vehicle) api.health(vehicle.vin).then((h) => (pushover = h.alerts.pushover)).catch(() => {});
    } catch (e) {
      error = e.message;
    }
  });
</script>

<header>
  <h1>Settings</h1>
</header>

{#if error}
  <div class="card err-box" role="alert">Could not save: {error}</div>
{/if}

{#if !all}
  <div class="muted">Loading…</div>
{:else}
  <section class="card">
    <div class="head">
      <h2>Units</h2>
      {#if saved === 'units'}<span class="ok small" role="status">Saved</span>{/if}
    </div>
    <p class="muted small">Applies to everyone viewing this dashboard.</p>

    <div class="field">
      <div>
        <div class="label">Distance</div>
        <div class="small muted">Trips, odometer, range and speeds</div>
      </div>
      <div class="seg" role="group" aria-label="Distance unit">
        <button class:on={prefs.distance === 'mi'} aria-pressed={prefs.distance === 'mi'} onclick={() => setGlobal({ distance_unit: 'mi' }, 'units')}>Miles</button>
        <button class:on={prefs.distance === 'km'} aria-pressed={prefs.distance === 'km'} onclick={() => setGlobal({ distance_unit: 'km' }, 'units')}>Kilometres</button>
      </div>
    </div>

    <div class="field">
      <div>
        <div class="label">Fuel economy</div>
        <div class="small muted">UK gallons for mpg</div>
      </div>
      <div class="seg" role="group" aria-label="Economy unit">
        <button class:on={prefs.economy === 'mpg'} aria-pressed={prefs.economy === 'mpg'} onclick={() => setGlobal({ economy_unit: 'mpg' }, 'units')}>mpg (UK)</button>
        <button class:on={prefs.economy === 'l100'} aria-pressed={prefs.economy === 'l100'} onclick={() => setGlobal({ economy_unit: 'l100' }, 'units')}>L/100 km</button>
      </div>
    </div>
  </section>

  {#if vehicle && car}
    <section class="card">
      <div class="head">
        <h2>{carName}</h2>
        {#if saved === 'car'}<span class="ok small" role="status">Saved. Fill-up costs update within a minute.</span>{/if}
      </div>

      <div class="field">
        <label for="grade">
          <div class="label">Fuel you buy</div>
          <div class="small muted">Used to look up station prices for your fill-ups and nearby prices</div>
        </label>
        <select id="grade" value={car.fuel_grade ?? ''} onchange={(e) => setVehicle({ fuel_grade: e.currentTarget.value || defaultGrade })}>
          {#if !car.fuel_grade}<option value="">Default: {all.grades[defaultGrade]}</option>{/if}
          {#each Object.entries(all.grades) as [code, label]}
            {#if (car.fuel_kind === 'diesel') === code.startsWith('B7')}
              <option value={code}>{label}</option>
            {/if}
          {/each}
        </select>
      </div>

      <div class="field">
        <label for="tank">
          <div class="label">Usable tank size</div>
          <div class="small muted">
            Only needed if your car reports fuel as a percentage but not litres.
            {#if car.tank_litres_detected}Worked out from your car's readings: about {num(car.tank_litres_detected)} L.{/if}
          </div>
        </label>
        <div class="inline">
          <input id="tank" type="number" inputmode="decimal" min="0" max="200" step="1" placeholder="auto" bind:value={tankInput} />
          <span class="muted">L</span>
          <button class="btn" onclick={saveTank}>Save</button>
        </div>
      </div>

      <div class="field">
        <label for="limit">
          <div class="label">Yearly mileage limit</div>
          <div class="small muted">From your insurance, PCP or lease. Insights tracks how you're doing against it. Leave empty for none.</div>
        </label>
        <div class="inline wrap">
          <input id="limit" class="wide" type="number" inputmode="numeric" min="0" step="500" placeholder="none" bind:value={limitInput} />
          <span class="muted">{prefs.distance === 'mi' ? 'mi' : 'km'} a year from</span>
          <input class="date" type="date" aria-label="Start of the mileage year" bind:value={limitStart} />
          <button class="btn" onclick={saveLimit}>Save</button>
        </div>
      </div>
    </section>
  {/if}

  {#if al}
    <section class="card">
      <div class="head">
        <h2>Alerts</h2>
        {#if saved === 'alerts'}<span class="ok small" role="status">Saved</span>{/if}
      </div>
      <p class="muted small">
        {#if pushover}Sent to your phone with Pushover, and listed on the Health page.
        {:else if pushover === false}Listed on the Health page. To get them on your phone, add <code>BIMMERDASH_PUSHOVER_TOKEN</code> and <code>BIMMERDASH_PUSHOVER_USER</code> to <code>.env</code>.
        {:else}Listed on the Health page, and sent with Pushover if it's set up.{/if}
      </p>

      <div class="field">
        <div><div class="label">Alerts</div><div class="small muted">Turn all of them off or on</div></div>
        <label class="switch"><input type="checkbox" checked={al.enabled} onchange={(e) => setAlerts({ enabled: e.currentTarget.checked })} aria-label="All alerts" /><span aria-hidden="true">{al.enabled ? 'On' : 'Off'}</span></label>
      </div>
      {#each ALERTS as [key, label, hint]}
        <div class="field" class:dim={!al.enabled}>
          <div><div class="label">{label}</div><div class="small muted">{hint}</div></div>
          <div class="inline">
            {#if key === 'low_fuel'}
              <span class="small muted">below</span>
              <select class="narrow" value={al.low_fuel_percent} onchange={(e) => setAlerts({ low_fuel_percent: Number(e.currentTarget.value) })} aria-label="Fuel level threshold">
                {#each [10, 15, 20, 25, 30, 40] as p}<option value={p}>{p}%</option>{/each}
              </select>
            {:else if key === 'left_open'}
              <span class="small muted">after</span>
              <select class="narrow" value={al.left_open_minutes} onchange={(e) => setAlerts({ left_open_minutes: Number(e.currentTarget.value) })} aria-label="Minutes parked before alerting">
                {#each [5, 10, 15, 30, 60] as m}<option value={m}>{m} min</option>{/each}
              </select>
            {/if}
            <label class="switch"><input type="checkbox" checked={al[key]} disabled={!al.enabled} onchange={(e) => setAlerts({ [key]: e.currentTarget.checked })} aria-label={label} /><span aria-hidden="true">{al[key] ? 'On' : 'Off'}</span></label>
          </div>
        </div>
      {/each}
    </section>
  {/if}

  {#if account?.enabled && account.user}
    <section class="card">
      <div class="head">
        <h2>Account</h2>
        <button class="btn" onclick={signOut}>Sign out</button>
      </div>
      <p class="muted small">Signed in as <strong>{account.user}</strong>.</p>
      <form class="pw" onsubmit={changePassword}>
        <input type="text" autocomplete="username" value={account.user} hidden readonly />
        <label><span class="label">Current password</span><input type="password" autocomplete="current-password" bind:value={pwCurrent} required /></label>
        <label><span class="label">New password</span><input type="password" autocomplete="new-password" bind:value={pwNew} required minlength="10" /></label>
        <label><span class="label">Repeat new password</span><input type="password" autocomplete="new-password" bind:value={pwRepeat} required /></label>
        <div class="inline">
          <button class="btn" type="submit">Change password</button>
          {#if pwMsg}<span class="ok small" role="status">{pwMsg}</span>{/if}
          {#if pwErr}<span class="bad-text small" role="alert">{pwErr}</span>{/if}
        </div>
      </form>
    </section>
  {:else if account && !account.enabled}
    <section class="card">
      <h2>Account</h2>
      <p class="muted small">Login is turned off (<code>BIMMERDASH_AUTH=off</code>). Only do this when something in front of bimmerdash already asks for a login.</p>
    </section>
  {/if}
{/if}

<style>
  header { display: flex; align-items: flex-end; }
  section { display: flex; flex-direction: column; gap: 4px; max-width: 760px; }
  .head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .ok { color: var(--ok); }
  .err-box { max-width: 760px; }
  .field { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; padding: 16px 0; border-top: 1px solid var(--line); }
  .field:first-of-type { border-top: 0; }
  .field label { flex: 1 1 260px; }
  .field > div:first-child { flex: 1 1 260px; }
  .label { font-weight: 500; margin-bottom: 2px; }
  select { min-width: 240px; max-width: 100%; }
  input:not([type='checkbox']) { width: 90px; }
  .inline { display: flex; align-items: center; gap: 8px; }
  .pw { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; max-width: 360px; }
  .pw label { display: flex; flex-direction: column; gap: 6px; }
  .pw input { width: 100%; }
  .inline.wrap { flex-wrap: wrap; }
  input.wide { width: 110px; }
  input.date { width: auto; }
  select.narrow { min-width: 96px; }
  .field label.switch { flex: none; }
  .dim { opacity: 0.55; }
  .switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; min-height: 42px; }
  .switch span { min-width: 26px; font-size: 13px; color: var(--muted); }
  code { font-size: 12px; background: var(--card-3); padding: 1px 5px; border-radius: 5px; }
</style>
