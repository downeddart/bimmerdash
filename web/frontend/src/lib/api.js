async function get(path) {
  const r = await fetch(`/api${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function put(path, body) {
  const r = await fetch(`/api${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export const api = {
  vehicles: () => get('/vehicles'),
  status: () => get('/status'),
  overview: (vin) => get(`/vehicles/${vin}/overview`),
  latest: (vin) => get(`/vehicles/${vin}/latest`),
  weekly: (vin, weeks = 8) => get(`/vehicles/${vin}/distance/weekly?weeks=${weeks}`),
  trips: (vin) => get(`/vehicles/${vin}/trips`),
  fuel: (vin) => get(`/vehicles/${vin}/fuel`),
  nearby: (vin, km = 5) => get(`/vehicles/${vin}/fuel/nearby?radius_km=${km}`),
  settings: () => get('/settings'),
  putSettings: (body) => put('/settings', body),
  putVehicleSettings: (vin, body) => put(`/vehicles/${vin}/settings`, body),
  imageUrl: (vin) => `/api/vehicles/${vin}/image`,
};
