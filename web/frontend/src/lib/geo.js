export function haversineM(a, b) {
  const r = (d) => (d * Math.PI) / 180;
  const dp = r(b.lat - a.lat);
  const dl = r(b.lon - a.lon);
  const h = Math.sin(dp / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(dl / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

// The closest named place whose radius covers this point, or null.
export function placeAt(places, point) {
  if (!point || !places?.length) return null;
  let best = null;
  for (const p of places) {
    const d = haversineM(point, p);
    if (d <= p.radius_m && (!best || d < best.d)) best = { d, p };
  }
  return best?.p ?? null;
}
