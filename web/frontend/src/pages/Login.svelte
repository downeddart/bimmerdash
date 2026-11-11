<script>
  import { api } from '../lib/api.js';

  // setup: no account exists yet, so this creates the first one.
  let { setup = false, onDone } = $props();
  let username = $state('');
  let password = $state('');
  let repeat = $state('');
  let error = $state(null);
  let busy = $state(false);

  const MIN = 10;

  async function submit(e) {
    e.preventDefault();
    error = null;
    if (setup && password.length < MIN) return (error = `Use at least ${MIN} characters.`);
    if (setup && password !== repeat) return (error = "Passwords don't match.");
    busy = true;
    try {
      await (setup ? api.setup(username.trim(), password) : api.login(username.trim(), password));
      password = repeat = '';
      onDone?.();
    } catch (err) {
      error = err.message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form class="card" onsubmit={submit} aria-labelledby="login-title">
    <div class="brand">
      <span class="logo" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0B0D10" stroke-width="2.4" stroke-linecap="round"><path d="M4 15a8 8 0 0 1 16 0" /><path d="M12 15l4-5" /></svg>
      </span>
      bimmerdash
    </div>
    <h1 id="login-title">{setup ? 'Create your account' : 'Sign in'}</h1>
    {#if setup}
      <p class="muted small">No account exists yet. The first account created here is the one that can sign in. To reset a password later, see the README.</p>
    {/if}

    <label>
      <span>Username</span>
      <input bind:value={username} autocomplete="username" autocapitalize="none" spellcheck="false" required maxlength="64" />
    </label>
    <label>
      <span>Password</span>
      <input type="password" bind:value={password} autocomplete={setup ? 'new-password' : 'current-password'} required minlength={setup ? MIN : 1} />
    </label>
    {#if setup}
      <label>
        <span>Repeat password</span>
        <input type="password" bind:value={repeat} autocomplete="new-password" required />
      </label>
    {/if}

    {#if error}<div class="err-box" role="alert">{error}</div>{/if}

    <button type="submit" disabled={busy}>{busy ? '…' : setup ? 'Create account' : 'Sign in'}</button>
  </form>
</div>

<style>
  .wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px 16px; }
  form { width: 100%; max-width: 380px; display: flex; flex-direction: column; gap: 16px; }
  .brand { display: flex; align-items: center; gap: 10px; font-family: var(--display); font-weight: 600; font-size: 18px; }
  .logo { width: 30px; height: 30px; border-radius: 9px; background: var(--accent); display: flex; align-items: center; justify-content: center; }
  h1 { font-size: 26px; }
  p { margin: -6px 0 0; line-height: 1.5; }
  label { display: flex; flex-direction: column; gap: 6px; font-weight: 500; }
  input { padding: 11px 12px; min-height: 44px; }
  button { margin-top: 4px; border: 0; border-radius: 10px; background: var(--accent); color: #0b0d10; font-weight: 600; padding: 12px; min-height: 46px; cursor: pointer; }
  button:hover { background: var(--accent-hover); }
  button:disabled { opacity: 0.6; cursor: default; }
  .err-box { padding: 10px 12px; border-radius: 10px; font-size: 13px; }
</style>
