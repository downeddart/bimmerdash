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
  import Skeleton from './components/Skeleton.svelte';

  const routes = {
    // tab: shown in the phone tab bar; the rest sit in the phone header.
    '/': { label: 'Overview', icon: 'grid', tab: true },
    '/trips': { label: 'Trips', icon: 'route', tab: true },
    '/fuel': { label: 'Fuel', icon: 'fuel', tab: true },
    '/insights': { label: 'Insights', icon: 'chart', tab: true },
    '/health': { label: 'Health', icon: 'health', tab: true },
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

  const streamColour = $derived(streamAge < 3600 ? 'var(--ok)' : streamAge < 86400 ? 'var(--warn)' : 'var(--idle)');
  const streamLabel = $derived(streamAge < 3600 ? 'Stream active' : status?.last_message_at ? 'Stream quiet' : 'No data yet');
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

{#snippet icon(name)}
  <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    {#if name === 'grid'}
      <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
    {:else if name === 'route'}
      <circle cx="6" cy="19" r="2.5" /><circle cx="18" cy="5" r="2.5" /><path d="M8.5 19H16a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h7.5" />
    {:else if name === 'fuel'}
      <path d="M4 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16" /><path d="M3 21h13" /><path d="M15 9h2a2 2 0 0 1 2 2v5a1.5 1.5 0 0 0 3 0V8l-3-3" /><path d="M7 8h5" />
    {:else if name === 'chart'}
      <path d="M4 20V10" /><path d="M10 20V4" /><path d="M16 20v-7" /><path d="M22 20H2" />
    {:else if name === 'health'}
      <path d="M3 12h4l2-5 4 10 2-5h6" />
    {:else if name === 'gear'}
      <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    {:else}
      <path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" />
    {/if}
  </svg>
{/snippet}

{#snippet logo()}
  <span class="logo" aria-hidden="true">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 15a8 8 0 0 1 16 0" /><path d="M12 15l4-5" /></svg>
  </span>
{/snippet}

<div class="shell">
  <!-- Phones: a slim header; the main links move to a tab bar at the bottom. -->
  <header class="mbar">
    <a class="brand" href="#/">{@render logo()}bimmerdash</a>
    <span class="mstatus" title={streamLabel}><span class="dot" style:background={streamColour}></span><span class="sr-only">{streamLabel}</span></span>
    <a class="mlink" href="#/data" class:active={path === '/data'} aria-current={path === '/data' ? 'page' : undefined}>{@render icon('list')}<span class="sr-only">Data</span></a>
    <a class="mlink" href="#/settings" class:active={path === '/settings'} aria-current={path === '/settings' ? 'page' : undefined}>{@render icon('gear')}<span class="sr-only">Settings</span></a>
  </header>

  <nav aria-label="Main">
    <a class="brand" href="#/">{@render logo()}bimmerdash</a>
    <div class="links">
      {#each Object.entries(routes) as [href, r]}
        <a href={'#' + href} class:active={path === href} class:wide-only={!r.tab} aria-current={path === href ? 'page' : undefined}>
          {@render icon(r.icon)}
          <span class="label">{r.label}</span>
        </a>
      {/each}
    </div>
    <div class="stream">
      <div class="row">
        <span class="dot" style:background={streamColour}></span>
        {streamLabel}
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
      <Skeleton />
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
  .logo { width: 30px; height: 30px; border-radius: 8px; color: var(--bg); background: var(--accent); display: flex; align-items: center; justify-content: center; flex: none; }
  .links { display: flex; flex-direction: column; gap: 4px; }
  .links a { display: flex; align-items: center; gap: 12px; padding: 11px 12px; border-radius: 8px; color: var(--muted); font-weight: 500; min-height: 44px; }
  .links a:hover { color: var(--text); background: var(--card); }
  .links a.active { background: var(--card-2); color: var(--text); }
  .stream { margin-top: auto; padding: 14px; border-radius: 8px; background: var(--card); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; }
  .row { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 500; }
  .row.small { font-size: 12px; font-weight: 400; color: var(--muted); }
  .sep { height: 1px; background: var(--line); margin: 4px 0; }
  .mbar { display: none; }
  @media (min-width: 821px) {
    /* Keep the nav (and its status box) on screen while long pages scroll. */
    nav { position: sticky; top: 0; height: 100vh; align-self: flex-start; overflow-y: auto; }
  }
  main:focus { outline: none; }
  main { flex: 999 1 560px; min-width: 0; padding: 32px clamp(16px, 3vw, 40px) 48px; display: flex; flex-direction: column; gap: 24px; }

  @media (max-width: 820px) {
    /* Phone: slim header on top, the five main pages as a tab bar at the bottom. */
    .mbar {
      display: flex; align-items: center; gap: 4px; width: 100%;
      position: sticky; top: 0; z-index: 20;
      padding: 8px 8px 8px 12px; background: var(--nav); border-bottom: 1px solid var(--line);
    }
    .mbar .brand { padding: 0; font-size: 17px; margin-right: auto; }
    .mbar .logo { width: 28px; height: 28px; }
    .mstatus { display: grid; place-items: center; width: 44px; height: 44px; }
    .mlink { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 8px; color: var(--muted); }
    .mlink.active { color: var(--text); background: var(--card-2); }
    nav {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
      flex: none; width: 100%; padding: 0 0 env(safe-area-inset-bottom);
      border-right: 0; border-top: 1px solid var(--line);
    }
    nav > .brand, .stream, .links a.wide-only { display: none; }
    .links { flex-direction: row; gap: 0; }
    .links a {
      flex: 1 1 0; flex-direction: column; gap: 3px; justify-content: center;
      padding: 8px 2px; min-height: 58px; border-radius: 0;
      font-size: 11px; font-weight: 600;
    }
    .links a.active { background: none; color: var(--accent); }
    .links a:hover { background: none; }
    main { padding-top: 20px; padding-bottom: calc(80px + env(safe-area-inset-bottom)); }
  }
</style>
