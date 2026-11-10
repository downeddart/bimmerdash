// Display settings, shared by everyone via the server. A local copy avoids a flash of
// the wrong units on load; storage can be unavailable, so that is best-effort.
import { api } from './api.js';

const CACHE = 'bimmerdash.prefs';

function cached() {
  try {
    return JSON.parse(localStorage.getItem(CACHE)) ?? {};
  } catch {
    return {};
  }
}

export const prefs = $state({ distance: 'mi', economy: 'mpg', ...cached() });

function remember() {
  try {
    localStorage.setItem(CACHE, JSON.stringify({ distance: prefs.distance, economy: prefs.economy }));
  } catch {
    /* not cached */
  }
}

function apply(s) {
  prefs.distance = s.global.distance_unit;
  prefs.economy = s.global.economy_unit;
  remember();
  return s;
}

export async function loadSettings() {
  return apply(await api.settings());
}

export async function saveGlobal(patch) {
  if (patch.distance_unit) prefs.distance = patch.distance_unit;
  if (patch.economy_unit) prefs.economy = patch.economy_unit;
  return apply(await api.putSettings(patch));
}

export const setEconomyUnit = (unit) => saveGlobal({ economy_unit: unit });
