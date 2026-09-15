// Tiny fetch wrapper around /api. Vite proxies /api -> :8000 in dev;
// in prod FastAPI serves the built bundle so /api is same-origin.
const BASE = '/api';

async function req(path, opts = {}) {
  const r = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.status === 204 ? null : r.json();
}

const json = (method, body) => ({ method, body: JSON.stringify(body) });

export const api = {
  auth: () => req('/auth'),
  login: (pw) => req('/login', json('POST', { password: pw })),
  logout: () => req('/logout', { method: 'POST' }),
  accounts: () => req('/accounts'),
  createAccount: (a) => req('/accounts', json('POST', a)),
  updateAccount: (id, a) => req(`/accounts/${id}`, json('PUT', a)),
  deleteAccount: (id) => req(`/accounts/${id}`, { method: 'DELETE' }),
  runNow: (id) => req(`/accounts/${id}/run-now`, { method: 'POST' }),
  replan: (id) => req(`/accounts/${id}/replan`, { method: 'POST' }),
  schedule: (id) => req(`/accounts/${id}/schedule`),
  runs: (id) => req(`/accounts/${id}/runs`),
  actions: (id) => req(`/accounts/${id}/actions?limit=50`),
  runLog: (rid) => req(`/runs/${rid}/log?tail=200`),
  activity: () => req('/activity?limit=200'),
  clearErrorActivity: () => req('/activity/errors', { method: 'DELETE' }),
  today: (id) => req(`/accounts/${id}/today`),
  accountActivity: () => req('/accounts/activity'),
  settings: () => req('/settings'),
  updateSettings: (s) => req('/settings', json('PUT', s)),
  status: () => req('/status'),
  triggerTick: () => req('/scheduler/tick', { method: 'POST' }),
  proxies: () => req('/proxies'),
  proxy: (id) => req(`/proxies/${id}`),
  createProxy: (p) => req('/proxies', json('POST', p)),
  updateProxy: (id, p) => req(`/proxies/${id}`, json('PUT', p)),
  deleteProxy: (id) => req(`/proxies/${id}`, { method: 'DELETE' }),
  checkProxy: (id) => req(`/proxies/${id}/check`, { method: 'POST' }),
  testProxy: (p) => req('/proxies/test', json('POST', p)),
  proxyLog: () => req('/proxies/log?limit=50'),
  clearProxyLog: () => req('/proxies/log', { method: 'DELETE' }),
  raidRun: (b) => req('/raider/run', json('POST', b)),
  massPlan: (b) => req('/raider/mass/plan', json('POST', b)),
  massStart: (b) => req('/raider/mass/start', json('POST', b)),
  massFinish: (b) => req('/raider/mass/finish', json('POST', b)),
  raids: () => req('/raids?limit=50'),
  autoreplyOverview: () => req('/autoreply/overview'),
  autoreplyAssign: (b) => req('/autoreply/accounts', json('POST', b)),
  autoreplyUpdate: (id, b) => req(`/autoreply/accounts/${id}`, json('PUT', b)),
  autoreplyRemove: (id) => req(`/autoreply/accounts/${id}`, { method: 'DELETE' }),
  autoreplyRun: (id) => req(`/autoreply/accounts/${id}/run`, { method: 'POST' }),
  autoreplyReset: (id) => req(`/autoreply/accounts/${id}/reset`, { method: 'POST' }),
  autoreplyLog: (id) => req(`/autoreply/accounts/${id}/log`),
  autoreplyActivity: () => req('/autoreply/activity?limit=100'),
  dryRuns: () => req('/dry-runs'),
  dryRunReport: (runId) => req(`/dry-runs/${runId}/report`),
  dryRunSteps: (runId) => req(`/dry-runs/${runId}/steps`),
  dryRunScreenshot: (runId, file) => `/api/dry-runs/${runId}/screenshot/${encodeURIComponent(file)}`,
};
