<script>
  // Small Leaflet wrapper. Points are drawn as circle markers (no image assets needed).
  //   markers: [{ lat, lon, colour?, radius?, label? }]
  //   line:    [{ lat, lon }]  drawn dashed, as positions are sparse
  import { onMount } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';

  let { markers = [], line = [], height = 280, label = 'Map' } = $props();
  let el;
  let map;
  let layer;

  function draw() {
    if (!map) return;
    layer.clearLayers();
    const pts = [];
    if (line.length > 1) {
      const ll = line.map((p) => [p.lat, p.lon]);
      L.polyline(ll, { color: '#5aa2ff', weight: 3, opacity: 0.8, dashArray: '6 6' }).addTo(layer);
      pts.push(...ll);
    }
    for (const m of markers) {
      const c = L.circleMarker([m.lat, m.lon], {
        radius: m.radius ?? 7,
        color: '#0b0d10',
        weight: 2,
        fillColor: m.colour ?? '#5aa2ff',
        fillOpacity: 1,
      }).addTo(layer);
      if (m.label) c.bindTooltip(m.label);
      pts.push([m.lat, m.lon]);
    }
    if (pts.length === 1) map.setView(pts[0], 15);
    else if (pts.length > 1) map.fitBounds(pts, { padding: [28, 28], maxZoom: 16 });
  }

  onMount(() => {
    map = L.map(el, { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
    draw();
    return () => map.remove();
  });

  $effect(() => {
    // re-draw when inputs change
    markers;
    line;
    draw();
  });
</script>

<div class="map" bind:this={el} style:height={`${height}px`} role="img" aria-label={label}></div>

<style>
  .map { width: 100%; border-radius: 14px; overflow: hidden; background: #1a1f27; border: 1px solid var(--line); }
  /* Dark-mode the standard tiles rather than depend on a third-party dark style. */
  .map :global(.leaflet-tile-pane) { filter: invert(1) hue-rotate(180deg) brightness(0.9) contrast(0.9) saturate(0.6); }
  .map :global(.leaflet-control-attribution) { background: rgba(11, 13, 16, 0.75); color: var(--muted); }
  .map :global(.leaflet-control-attribution a) { color: var(--muted); }
  .map :global(.leaflet-bar a) { background: var(--card-2); color: var(--text); border-color: var(--line-2); }
  .map :global(.leaflet-tooltip) { background: var(--card-2); color: var(--text); border: 1px solid var(--line-2); box-shadow: none; }
</style>
