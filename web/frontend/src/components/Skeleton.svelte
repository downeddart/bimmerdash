<script>
  // Placeholder while a page loads: its title, a strip of figures and two panels.
  let { label = 'Loading' } = $props();
</script>

<div class="skel" role="status" aria-live="polite">
  <span class="sr-only">{label}…</span>
  <div class="sk title" aria-hidden="true"></div>
  <div class="sk strip" aria-hidden="true"></div>
  <div class="pair" aria-hidden="true"><div class="sk panel"></div><div class="sk panel"></div></div>
</div>

<style>
  .skel { display: flex; flex-direction: column; gap: 24px; }
  .sk { background: var(--card); border: 1px solid var(--line); border-radius: 12px; }
  .title { width: min(320px, 70%); height: 36px; border-radius: 8px; }
  .strip { height: 92px; }
  .pair { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); }
  .panel { height: 260px; }
  @media (prefers-reduced-motion: no-preference) {
    .sk { animation: breathe 1.4s ease-in-out infinite; }
    @keyframes breathe { 50% { background: var(--card-2); } }
  }
</style>
