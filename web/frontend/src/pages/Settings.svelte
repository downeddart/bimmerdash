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

  const car = $derived(vehicle ? all?.vehicles?.[vehicle.vin] : null);
  const carName = $derived(vehicle ? [vehicle.model_name, vehicle.body_type].filter(Boolean).join(' ') || 'Your car' : '');
  const defaultGrade = $derived(car?.fuel_kind === 'diesel' ? 'B7_STANDARD' : 'E10');

  // Vehicles can load after this page mounts.
  $effect(() => {
    if (car?.tank_litres != null && tankInput === '') tankInput = car.tank_litres;
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
    } catch (e) {
      error = e.message;
    }
  });
</script>

<header>
  <h1>Settings</h1>
</header>

{#if error}
  <div class="card err" role="alert">Could not save: {error}</div>
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
          {#if pwErr}<span class="bad small" role="alert">{pwErr}</span>{/if}
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
  .err { border-color: #5c2626; color: #ffb4b4; max-width: 760px; }
  .field { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; padding: 16px 0; border-top: 1px solid var(--line); }
  .field:first-of-type { border-top: 0; }
  .field label { flex: 1 1 260px; }
  .field > div:first-child { flex: 1 1 260px; }
  .label { font-weight: 500; margin-bottom: 2px; }
  .seg { display: inline-flex; padding: 3px; border-radius: 999px; background: var(--bg); border: 1px solid var(--line-2); }
  .seg button { border: 0; background: none; padding: 8px 16px; border-radius: 999px; cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 500; min-height: 40px; }
  .seg button.on { background: var(--card-3); color: var(--text); }
  select, input { font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line-2); border-radius: 10px; padding: 9px 12px; min-height: 42px; }
  select { min-width: 240px; max-width: 100%; }
  input { width: 90px; }
  .inline { display: flex; align-items: center; gap: 8px; }
  .btn { border: 1px solid var(--line-2); background: var(--card-3); border-radius: 10px; padding: 9px 16px; cursor: pointer; min-height: 42px; }
  .btn:hover { border-color: var(--accent); }
  .pw { display: flex; flex-direction: column; gap: 12px; margin-top: 8px; max-width: 360px; }
  .pw label { display: flex; flex-direction: column; gap: 6px; }
  .pw input { width: 100%; }
  .bad { color: #ffb4b4; }
  code { font-size: 12px; background: var(--card-3); padding: 1px 5px; border-radius: 5px; }
</style>
