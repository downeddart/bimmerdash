// Per-browser display preferences. Storage can be unavailable (private windows), so
// everything falls back to defaults.
function read(key, fallback) {
  try {
    return localStorage.getItem(`bimmerdash.${key}`) ?? fallback;
  } catch {
    return fallback;
  }
}

export const prefs = $state({ economy: read('economy', 'mpg') });

export function setEconomyUnit(unit) {
  prefs.economy = unit;
  try {
    localStorage.setItem('bimmerdash.economy', unit);
  } catch {
    /* not persisted */
  }
}
