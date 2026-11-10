const nf = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 });

export const DASH = '—';

export function num(v, decimals = 0) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return DASH;
  return (decimals ? nf1 : nf).format(Number(v));
}

export function ago(iso) {
  if (!iso) return 'never';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

export function title(text) {
  if (!text) return DASH;
  return String(text).toLowerCase().replace(/(^|[\s_])\w/g, (m) => m.toUpperCase()).replace(/_/g, ' ');
}

export function shortDate(iso) {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

export function display(value) {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function dateTime(iso) {
  return new Date(iso).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function time(iso) {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

export function longDate(iso) {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function duration(minutes) {
  if (minutes == null) return DASH;
  const m = Math.round(minutes);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} m`;
}

const gbp = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' });
export function money(v) {
  return v == null ? DASH : gbp.format(v);
}

export function monthName(ym) {
  const [y, m] = ym.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString('en-GB', { month: 'short' });
}

// UK gallons: mpg = 282.481 / (L/100 km)
export function economy(l100, unit) {
  if (l100 == null) return DASH;
  return unit === 'mpg' ? nf1.format(282.481 / l100) : nf1.format(l100);
}
export const economyUnit = (unit) => (unit === 'mpg' ? 'mpg (UK)' : 'L/100 km');

// Distances are stored in km; show them in the chosen unit.
const KM_PER_MI = 1.609344;
export const distUnit = (u) => (u === 'mi' ? 'mi' : 'km');
export const speedUnit = (u) => (u === 'mi' ? 'mph' : 'km/h');
export function toUnit(km, u) {
  return km == null ? null : u === 'mi' ? km / KM_PER_MI : km;
}
export function dist(km, u, decimals = 0) {
  return km == null ? DASH : num(toUnit(km, u), decimals);
}
// A value the car reported with its own unit (normally km).
export function measure(m, u) {
  if (!m) return DASH;
  const isKm = !m.unit || m.unit === 'km';
  return isKm ? `${dist(m.value, u)} ${distUnit(u)}` : `${num(m.value)} ${m.unit}`;
}
export function perDistance(pencePerKm, u) {
  return pencePerKm == null ? null : u === 'mi' ? pencePerKm * KM_PER_MI : pencePerKm;
}
