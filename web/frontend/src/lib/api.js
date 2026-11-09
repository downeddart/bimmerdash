async function get(path) {
  const r = await fetch(`/api${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export const api = {
  vehicles: () => get('/vehicles'),
  status: () => get('/status'),
  overview: (vin) => get(`/vehicles/${vin}/overview`),
  latest: (vin) => get(`/vehicles/${vin}/latest`),
  weekly: (vin, weeks = 8) => get(`/vehicles/${vin}/distance/weekly?weeks=${weeks}`),
  imageUrl: (vin) => `/api/vehicles/${vin}/image`,
};
