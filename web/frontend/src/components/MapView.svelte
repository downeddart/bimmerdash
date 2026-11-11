<script>
  // Small Leaflet wrapper. Points are drawn as circle markers (no image assets needed).
  //   markers: [{ lat, lon, colour?, radius?, label? }]
  //   line:    [{ lat, lon }]  drawn dashed, as positions are sparse
  // Leaflet is loaded on first use, so pages without a map never download it.
  import { onMount } from 'svelte';

  let L;

  let { markers = [], line = [], height = 280, label = 'Map' } = $props();
  let el;
  let map;
  let layer;

  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Marker colours are theme names ('accent', 'ok', 'warn', 'text', 'muted').
  const colour = (name) => token(`--${name ?? 'accent'}`);

  function draw() {
    if (!map) return;
    const accent = colour('accent');
    layer.clearLayers();
    const pts = [];
    if (line.length > 1) {
      const ll = line.map((p) => [p.lat, p.lon]);
      L.polyline(ll, { color: accent, weight: 3, opacity: 0.8, dashArray: '6 6' }).addTo(layer);
      pts.push(...ll);
    }
    for (const m of markers) {
      const c = L.circleMarker([m.lat, m.lon], {
        radius: m.radius ?? 7,
        color: token('--bg'),
        weight: 2,
        fillColor: colour(m.colour),
        fillOpacity: 1,
      }).addTo(layer);
      if (m.label) c.bindTooltip(m.label);
      pts.push([m.lat, m.lon]);
    }
    if (pts.length === 1) map.setView(pts[0], 15);
    else if (pts.length > 1) map.fitBounds(pts, { padding: [28, 28], maxZoom: 16 });
  }

  onMount(() => {
    let gone = false;
    (async () => {
      const [mod] = await Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')]);
      if (gone) return;
      L = mod.default;
      init();
    })();
    return () => {
      gone = true;
      map?.remove();
    };
  });

  function init() {
    map = L.map(el, { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
    draw();
  }

  $effect(() => {
    // re-draw when inputs change
    markers;
    line;
    draw();
  });
</script>

<div class="map" bind:this={el} style:height={`${height}px`} role="region" aria-label={label}></div>

<style>
  .map { width: 100%; border-radius: 8px; overflow: hidden; background: var(--card-3); border: 1px solid var(--line); }
  /* Dark-mode the standard tiles rather than depend on a third-party dark style. */
  .map :global(.leaflet-tile-pane) { filter: invert(1) hue-rotate(180deg) brightness(0.9) contrast(0.9) saturate(0.6); }
  .map :global(.leaflet-control-attribution) { background: rgba(11, 13, 16, 0.8); color: var(--muted); font-size: 11px; padding: 2px 8px; border-top-left-radius: 8px; }
  .map :global(.leaflet-control-attribution a) { color: var(--muted); }
  .map :global(.leaflet-bar a) { background: var(--card-2); color: var(--text); border-color: var(--line-2); }
  .map :global(.leaflet-tooltip) { background: var(--card-2); color: var(--text); border: 1px solid var(--line-2); box-shadow: none; }
</style>
