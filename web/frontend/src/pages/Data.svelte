<script>
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { ago, display } from '../lib/format.js';

  let { vehicle } = $props();
  let rows = $state([]);
  let filter = $state('');
  let shown = $derived(
    rows.filter((r) => r.key.toLowerCase().includes(filter.trim().toLowerCase()))
  );

  onMount(async () => {
    rows = await api.latest(vehicle.vin);
  });
</script>

<header>
  <h1>Data</h1>
  <p class="muted">The latest raw CarData value of every data point your car has sent: {rows.length} so far.</p>
</header>

<div class="card table-card">
  <label class="search">
    <span class="small muted">Filter keys</span>
    <input type="search" bind:value={filter} placeholder="e.g. fuel, tire, location" />
  </label>
  <div class="scroll">
    <table>
      <thead>
        <tr><th scope="col">Key</th><th scope="col">Value</th><th scope="col">Updated</th></tr>
      </thead>
      <tbody>
        {#each shown as r (r.key)}
          <tr>
            <td class="key">{r.key}</td>
            <td>{display(r.value)}{r.unit ? ` ${r.unit}` : ''}</td>
            <td class="muted">{ago(r.time)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

<style>
  header { display: flex; flex-direction: column; gap: 6px; }
  header p { margin: 4px 0 0; }
  .table-card { padding: 0; overflow: hidden; }
  .search { display: flex; flex-direction: column; gap: 6px; padding: 16px 20px; border-bottom: 1px solid var(--line); }
  input { max-width: 360px; min-height: 44px; }
  .scroll { overflow-x: auto; }
  table { width: 100%; min-width: 640px; border-collapse: collapse; }
  th { text-align: left; font-weight: 500; font-size: 12px; color: var(--muted); padding: 10px 20px; }
  td { padding: 10px 20px; border-top: 1px solid var(--line); vertical-align: top; }
  .key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; word-break: break-all; }
</style>
