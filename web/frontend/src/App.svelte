<script>
  import { onMount } from 'svelte';
  import { api, UNAUTHORISED } from './lib/api.js';
  import Login from './pages/Login.svelte';
  import { ago, shortDate } from './lib/format.js';
  import Overview from './pages/Overview.svelte';
  import Data from './pages/Data.svelte';
  import Trips from './pages/Trips.svelte';
  import Fuel from './pages/Fuel.svelte';
  import Settings from './pages/Settings.svelte';
  import Insights from './pages/Insights.svelte';
  import Health from './pages/Health.svelte';
  import { loadSettings } from './lib/prefs.svelte.js';

  const routes = {
    '/': { label: 'Overview', icon: 'grid' },
    '/trips': { label: 'Trips', icon: 'route' },
    '/fuel': { label: 'Fuel', icon: 'fuel' },
    '/insights': { label: 'Insights', icon: 'chart' },
    '/health': { label: 'Health', icon: 'health' },
    '/data': { label: 'Data', icon: 'list' },
    '/settings': { label: 'Settings', icon: 'gear' },
  };

  let path = $state(currentPath());
  let vehicles = $state(null);
  let error = $state(null);
  let status = $state(null);
  let authInfo = $state(null); // { enabled, setup_required, user }
  let authError = $state(null);
  const signedIn = $derived(authInfo && (!authInfo.enabled || authInfo.user));
  let vehicle = $derived(vehicles?.[0] ?? null);
  let streamAge = $derived(
    status?.last_message_at ? (Date.now() - new Date(status.last_message_at)) / 1000 : Infinity
  );

  const DOT = { ok: 'var(--ok)', stale: 'var(--warn)', missing: 'var(--warn)', off: 'var(--idle)' };
  let prices = $derived(status?.prices ?? null);
  let ukLabel = $derived.by(() => {
    const u = prices?.uk_averages;
    if (!u) return null;
    if (u.status === 'missing') return 'UK averages not loaded yet';
    const to = `to ${shortDate(u.latest_week)}`;
    return u.status === 'stale' ? `UK averages out of date (${to})` : `UK averages ${to}`;
  });
  let stationLabel = $derived.by(() => {
    const st = prices?.station_prices;
    if (!st) return null;
    if (st.status === 'off') return 'Station prices off';
    return st.status === 'stale'
      ? `Station prices stale (${ago(st.synced_at)})`
      : `Station prices ${ago(st.synced_at)}`;
  });

  $effect(() => {
    document.title = signedIn ? `${routes[path].label} · bimmerdash` : 'Sign in · bimmerdash';
  });

  function currentPath() {
    const p = location.hash.replace(/^#/, '') || '/';
    return p in routes ? p : '/';
  }

  async function loadStatus() {
    try {
      status = await api.status();
    } catch {
      status = null;
    }
  }

  async function checkAuth() {
    try {
      authInfo = await api.authState();
      authError = null;
    } catch (e) {
      authError = e.message;
    }
    if (signedIn) loadApp();
  }

  function loadApp() {
    error = null;
    api.vehicles().then((v) => (vehicles = v)).catch((e) => (error = e.message));
    loadStatus();
    loadSettings().catch(() => {});
  }

  onMount(() => {
    const onHash = () => (path = currentPath());
    const onUnauthorised = () => authInfo && (authInfo = { ...authInfo, user: null });
    window.addEventListener('hashchange', onHash);
    window.addEventListener(UNAUTHORISED, onUnauthorised);
    checkAuth();
    const t = setInterval(() => signedIn && loadStatus(), 60_000);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener(UNAUTHORISED, onUnauthorised);
      clearInterval(t);
    };
  });
</script>

{#if !authInfo}
  <div class="gate muted">{authError ? `Could not reach bimmerdash: ${authError}` : 'Loading…'}</div>
{:else if !signedIn}
  <Login setup={authInfo.setup_required} onDone={checkAuth} />
{:else}
<a class="skip-link" href="#main" onclick={(e) => { e.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to content</a>
<div class="shell">
  <nav aria-label="Main">
    <a class="brand" href="#/">
      <span class="logo" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 15a8 8 0 0 1 16 0" /><path d="M12 15l4-5" /></svg>
      </span>
      bimmerdash
    </a>
    <div class="links">
      {#each Object.entries(routes) as [href, r]}
        <a href={'#' + href} class:active={path === href} aria-current={path === href ? 'page' : undefined}>
          <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            {#if r.icon === 'grid'}
              <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
            {:else if r.icon === 'route'}
              <circle cx="6" cy="19" r="2.5" /><circle cx="18" cy="5" r="2.5" /><path d="M8.5 19H16a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h7.5" />
            {:else if r.icon === 'fuel'}
              <path d="M4 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16" /><path d="M3 21h13" /><path d="M15 9h2a2 2 0 0 1 2 2v5a1.5 1.5 0 0 0 3 0V8l-3-3" /><path d="M7 8h5" />
            {:else if r.icon === 'chart'}
              <path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" />
            {:else if r.icon === 'health'}
              <path d="M3 12h4l2-5 4 10 2-5h6" />
            {:else if r.icon === 'gear'}
              <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
            {:else}
              <path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" />
            {/if}
          </svg>
          {r.label}
        </a>
      {/each}
    </div>
    <div class="stream">
      <div class="row">
        <span class="dot" style:background={streamAge < 3600 ? 'var(--ok)' : streamAge < 86400 ? 'var(--warn)' : 'var(--idle)'}></span>
        {streamAge < 3600 ? 'Stream active' : status?.last_message_at ? 'Stream quiet' : 'No data yet'}
      </div>
      <div class="small muted">Last message {ago(status?.last_message_at)}</div>
      {#if status}<div class="small muted">API calls today: {status.api_calls_24h} / 50</div>{/if}
      {#if prices}
        <div class="sep" role="presentation"></div>
        <div class="row small" title="UK weekly average pump prices (DESNZ), used to estimate older fill-ups">
          <span class="dot" style:background={DOT[prices.uk_averages.status]}></span>{ukLabel}
        </div>
        <div class="row small" title={prices.station_prices.status === 'off' ? 'Add Fuel Finder credentials to .env to record station prices' : 'Live forecourt prices from the UK Fuel Finder scheme'}>
          <span class="dot" style:background={DOT[prices.station_prices.status]}></span>{stationLabel}
        </div>
      {/if}
    </div>
  </nav>

  <main id="main" tabindex="-1">
    {#if path === '/settings'}
      <Settings {vehicle} account={authInfo} onSignedOut={checkAuth} />
    {:else if error}
      <div class="card">Could not reach the bimmerdash API: {error}</div>
    {:else if vehicles === null}
      <div class="muted">Loading…</div>
    {:else if !vehicle}
      <div class="card">
        <h2>No car yet</h2>
        <p class="muted">Data appears once the collector receives its first message. Locking the car from the MyBMW app is a quick way to trigger one.</p>
      </div>
    {:else if path === '/'}
      <Overview {vehicle} />
    {:else if path === '/data'}
      <Data {vehicle} />
    {:else if path === '/insights'}
      <Insights {vehicle} />
    {:else if path === '/health'}
      <Health {vehicle} />
    {:else if path === '/trips'}
      <Trips {vehicle} />
    {:else}
      <Fuel {vehicle} />
    {/if}
  </main>
</div>
{/if}

<style>
  .gate { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 16px; }
  .shell { min-height: 100vh; display: flex; flex-wrap: wrap; align-content: flex-start; }
  nav {
    flex: 1 1 232px;
    max-width: 100%;
    padding: 28px 20px;
    border-right: 1px solid var(--line);
    background: var(--nav);
    display: flex;
    flex-direction: column;
    gap: 28px;
  }
  .brand { display: flex; align-items: center; gap: 10px; padding: 0 8px; color: var(--text); font-family: var(--display); font-weight: 600; font-size: 19px; }
  .logo { width: 30px; height: 30px; border-radius: 8px; color: var(--bg); background: var(--accent); display: flex; align-items: center; justify-content: center; }
  .links { display: flex; flex-direction: column; gap: 4px; }
  .links a { display: flex; align-items: center; gap: 12px; padding: 11px 12px; border-radius: 8px; color: var(--muted); font-weight: 500; min-height: 44px; }
  .links a:hover { color: var(--text); }
  .links a.active { background: var(--card-2); color: var(--text); }
  .stream { margin-top: auto; padding: 14px; border-radius: 8px; background: var(--card); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; }
  .row { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 500; }
  .row.small { font-size: 12px; font-weight: 400; color: var(--muted); }
  .sep { height: 1px; background: var(--line); margin: 4px 0; }
  @media (min-width: 821px) {
    /* Keep the nav (and its status box) on screen while long pages scroll. */
    nav { position: sticky; top: 0; height: 100vh; align-self: flex-start; overflow-y: auto; }
  }
  main:focus { outline: none; }
  main { flex: 999 1 560px; min-width: 0; padding: 32px clamp(16px, 3vw, 40px) 48px; display: flex; flex-direction: column; gap: 24px; }

  @media (max-width: 820px) {
    nav { border-right: 0; border-bottom: 1px solid var(--line); padding: 16px; gap: 14px; }
    .links { flex-direction: row; overflow-x: auto; }
    .links a { flex: none; }
    .stream { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding: 10px 12px; }
    .stream > .small.muted, .sep { display: none; }
  }
</style>
