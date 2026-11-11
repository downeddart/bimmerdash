// Every call goes through here, so a 401 (session expired, signed out elsewhere) can
// send the app back to the login screen.
export const UNAUTHORISED = 'bimmerdash:unauthorised';

async function request(method, path, body) {
  const r = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  });
  if (r.status === 401 && !path.startsWith('/auth/')) {
    window.dispatchEvent(new Event(UNAUTHORISED));
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      if (typeof j.detail === 'string') detail = j.detail;
      else if (Array.isArray(j.detail)) detail = j.detail.map((d) => d.msg).join('; ');
    } catch {
      /* not JSON */
    }
    const e = new Error(detail);
    e.status = r.status;
    throw e;
  }
  return r.json();
}

const get = (path) => request('GET', path);
const put = (path, body) => request('PUT', path, body);
const post = (path, body = {}) => request('POST', path, body);
const del = (path) => request('DELETE', path);

export const api = {
  vehicles: () => get('/vehicles'),
  status: () => get('/status'),
  overview: (vin) => get(`/vehicles/${vin}/overview`),
  latest: (vin) => get(`/vehicles/${vin}/latest`),
  weekly: (vin, weeks = 8) => get(`/vehicles/${vin}/distance/weekly?weeks=${weeks}`),
  trips: (vin) => get(`/vehicles/${vin}/trips`),
  fuel: (vin) => get(`/vehicles/${vin}/fuel`),
  nearby: (vin, km = 5) => get(`/vehicles/${vin}/fuel/nearby?radius_km=${km}`),
  usage: (vin) => get(`/vehicles/${vin}/usage`),
  health: (vin) => get(`/vehicles/${vin}/health`),
  advisor: (vin) => get(`/vehicles/${vin}/fuel/advisor`),
  places: () => get('/places'),
  addPlace: (place) => post('/places', place),
  renamePlace: (id, name) => put(`/places/${id}`, { name }),
  deletePlace: (id) => del(`/places/${id}`),
  settings: () => get('/settings'),
  putSettings: (body) => put('/settings', body),
  putVehicleSettings: (vin, body) => put(`/vehicles/${vin}/settings`, body),
  authState: () => get('/auth/state'),
  login: (username, password) => post('/auth/login', { username, password }),
  setup: (username, password) => post('/auth/setup', { username, password }),
  logout: () => post('/auth/logout'),
  changePassword: (current, next) => post('/auth/password', { current, new: next }),
  imageUrl: (vin) => `/api/vehicles/${vin}/image`,
};
