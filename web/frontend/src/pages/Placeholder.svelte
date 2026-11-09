<script>
  let { title, text } = $props();
</script>

<header>
  <div class="eyebrow">Coming soon</div>
  <h1>{title}</h1>
</header>
<div class="card empty">
  <p>{text}</p>
</div>

<style>
  header { display: flex; flex-direction: column; gap: 6px; }
  .empty { max-width: 640px; color: var(--muted); line-height: 1.6; }
  p { margin: 0; }
</style>
