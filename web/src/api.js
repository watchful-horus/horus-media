// Any API call that comes back 401 means the login expired: tell the app to show the login screen.
const AUTH_PATHS = ['/api/auth/login', '/api/auth/setup', '/api/auth/status']

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    if (res.status === 401 && !AUTH_PATHS.includes(path)) window.dispatchEvent(new Event('horus:login'))
    const detail = await res.json().catch(() => ({}))
    throw new Error(typeof detail.detail === 'string' ? detail.detail : `HTTP ${res.status}`)
  }
  return res.status === 204 ? null : res.json()
}

async function upload(path, file, headers = {}) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream', ...headers },
    body: file,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(typeof detail.detail === 'string' ? detail.detail : `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  uploadBackup: (file) => upload('/api/backups/upload', file),
  restoreAtSetup: (file) => upload('/api/auth/restore', file),
  deleteBackup: (name) => request('DELETE', `/api/backups/${name}`),
  authStatus: () => request('GET', '/api/auth/status'),
  setup: (username, password) => request('POST', '/api/auth/setup', { username, password }),
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  users: () => request('GET', '/api/users'),
  createUser: (u) => request('POST', '/api/users', u),
  updateUser: (id, changes) => request('PATCH', `/api/users/${id}`, changes),
  deleteUser: (id) => request('DELETE', `/api/users/${id}`),
  logout: () => request('POST', '/api/auth/logout'),
  changePassword: (current, next) => request('POST', '/api/auth/password', { current, new: next }),
  myKeys: () => request('GET', '/api/my-keys'),
  createMyKey: (name) => request('POST', '/api/my-keys', { name }),
  revokeMyKey: (id) => request('DELETE', `/api/my-keys/${id}`),
  report: (range) => request('GET', `/api/report?${range}`),
  sendReport: (range) => request('POST', `/api/report/send?${range}`),
  notifications: () => request('GET', '/api/notifications'),
  addChannel: (c) => request('POST', '/api/notifications', c),
  updateChannel: (id, changes) => request('PATCH', `/api/notifications/${id}`, changes),
  deleteChannel: (id) => request('DELETE', `/api/notifications/${id}`),
  testChannel: (id) => request('POST', `/api/notifications/${id}/test`),
  seerr: () => request('GET', '/api/seerr'),
  saveSeerr: (s) => request('PUT', '/api/seerr', s),
  deleteSeerr: () => request('DELETE', '/api/seerr'),
  testSeerr: () => request('POST', '/api/seerr/test'),
  seerrRequests: () => request('GET', '/api/seerr/requests'),
  keys: () => request('GET', '/api/keys'),
  createKey: (name, scope = 'read', days = null) =>
    request('POST', '/api/keys', { name, scope, expires_in_days: scope === 'control' ? days : null }),
  revokeKey: (id) => request('DELETE', `/api/keys/${id}`),
  restart: () => request('POST', '/api/restart'),
  shutdown: () => request('POST', '/api/shutdown'),
  activity: () => request('GET', '/api/activity'),
  stopStream: (body) => request('POST', '/api/activity/stop', body),
  plexLinkStart: () => request('POST', '/api/plex/link'),
  plexLinkStatus: (pin) => request('GET', `/api/plex/link/${pin}`),
  plexLinkAdd: (pin, body) => request('POST', `/api/plex/link/${pin}/add`, body),
  settings: () => request('GET', '/api/settings'),
  saveSettings: (s) => request('PUT', '/api/settings', s),
  backups: () => request('GET', '/api/backups'),
  createBackup: () => request('POST', '/api/backups'),
  restoreBackup: (name) => request('POST', `/api/backups/${name}/restore`),
  logs: ({ level, q, limit }) =>
    request('GET', `/api/logs?level=${level}&limit=${limit}&q=${encodeURIComponent(q ?? '')}`),
  clearLogs: () => request('DELETE', '/api/logs'),
  updateCheck: () => request('GET', '/api/update-check'),
  stats: (start, end, hourly = false) =>
    request('GET', `/api/stats?start=${start}&end=${end}${hourly ? '&hourly=true' : ''}`),
  servers: () => request('GET', '/api/servers'),
  addServer: (s) => request('POST', '/api/servers', s),
  renameServer: (id, name) => request('PATCH', `/api/servers/${id}`, { name }),
  deleteServer: (id) => request('DELETE', `/api/servers/${id}`),
  importHistory: (id) => request('POST', `/api/servers/${id}/import-history`),
  testSaved: (id) => request('POST', `/api/servers/${id}/test`),
  testUnsaved: (s) => request('POST', '/api/servers/test', s),
}
