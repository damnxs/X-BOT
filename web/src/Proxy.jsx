import { useEffect, useState } from 'react';
import { api } from './api.js';
import { fmtJakarta } from './Accounts.jsx';
import { useActions, useToast, Spinner } from './toast.jsx';

const empty = { name: '', host: '', port: 1080, scheme: 'http', username: '', password: '', active: true };

// Parse a pasted proxy endpoint into {host, port, username, password, scheme}.
// Accepts: user:pass:host:port (9proxy style) | host:port | user:pass@host:port
//        | scheme://user:pass@host:port | scheme://host:port
function parseProxyString(raw) {
  const s = (raw || '').trim();
  if (!s) return null;
  let scheme = 'http', body = s;
  const sm = s.match(/^(https?|socks5h?):\/\//i);
  if (sm) { scheme = sm[1].toLowerCase().replace('socks5h', 'socks5'); body = s.slice(sm[0].length); }

  const at = body.split('@');
  let userpass = '', hostport;
  if (at.length >= 2) {                       // ...@host:port
    userpass = at.slice(0, -1).join('@');
    hostport = at[at.length - 1];
  } else {
    hostport = body;
  }
  const c = hostport.split(':').map((x) => x.trim());
  let host, port, username = '', password = '';
  if (userpass) {
    if (c.length < 2) return null;
    port = c.pop(); host = c.join(':');
    const up = userpass.split(':');
    username = up[0]; password = up.slice(1).join(':');
  } else if (c.length === 4) {                // user:pass:host:port (no @)
    [username, password, host, port] = c;
  } else if (c.length === 2) {                // host:port
    [host, port] = c;
  } else {
    return null;
  }
  if (!host || !port || !/^\d+$/.test(port)) return null;
  return { host, port: +port, username, password, scheme };
}

// monoline icons — stroke=currentColor, sized by their container's CSS
const S = { strokeLinecap: 'round', strokeLinejoin: 'round', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6 };
const Icon = {
  shield:    (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/></svg>),
  plug:      (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0V8zM12 16v6"/></svg>),
  clipboard: (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><rect x="8" y="3" width="8" height="4" rx="1"/><path d="M9 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3"/></svg>),
  plus:      (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 5v14M5 12h14"/></svg>),
  refresh:   (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/></svg>),
  pencil:    (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>),
  power:     (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/></svg>),
  trash:     (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>),
  check:     (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M20 6 9 17l-5-5"/></svg>),
  x:         (p) => (<svg viewBox="0 0 24 24" {...S} {...p}><path d="M18 6 6 18M6 6l12 12"/></svg>),
};

export default function Proxy() {
  const [items, setItems] = useState(null);
  const [log, setLog] = useState([]);
  const [form, setForm] = useState(empty);
  const [testResult, setTestResult] = useState(null);
  const [msg, setMsg] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const { act, has } = useActions();
  const toast = useToast();

  const load = () => api.proxies().then(setItems).catch(() => {});
  const loadLog = () => api.proxyLog().then(setLog).catch(() => {});
  useEffect(() => {
    load(); loadLog();
    const i = setInterval(() => { load(); loadLog(); }, 5000);
    return () => clearInterval(i);
  }, []);

  const set = (k, v) => { setForm((p) => ({ ...p, [k]: v })); setTestResult(null); };
  const onPaste = (e) => {
    const p = parseProxyString(e.target.value);
    if (p) {
      setForm({ ...empty, ...p, name: form.name, active: form.active });
      setTestResult(null);
      setMsg('parsed ✓ — tweak if needed, then test connection');
    } else if (e.target.value.trim()) {
      setMsg("couldn't parse — fill the fields manually below");
    }
  };
  const test = (e) => {
    e.preventDefault();
    if (!form.host || !form.port) { setMsg('host, port required'); return; }
    setMsg('');
    act('test', async () => {
      const r = await api.testProxy({ host: form.host, port: +form.port, scheme: form.scheme, username: form.username, password: form.password });
      setTestResult(r);
      if (r.alive) toast.success('proxy connected · ' + (r.ip || ''));
      else toast.error('proxy did not connect');
    });
  };
  const add = (e) => {
    e.preventDefault();
    if (!testResult?.results?.some((r) => r.target === 'HTTPS' && r.ok)) {
      setMsg('HTTPS must pass before adding — the bot needs it'); return;
    }
    act('add', async () => {
      await api.createProxy({ ...form, port: +form.port });
      setForm(empty); setTestResult(null);
      toast.success('proxy added');
      load();
    });
  };
  const check = (id) => act('check-' + id, async () => {
    await api.checkProxy(id);
    toast.success('check complete');
    load(); loadLog();
  });
  const toggle = (p) => act('toggle-' + p.id, async () => { await api.updateProxy(p.id, { active: !p.active }); load(); });
  const del = (p) => act('del-' + p.id, async () => {
    if (!confirm('Delete ' + p.name + '?')) return;
    await api.deleteProxy(p.id); toast.success('proxy deleted'); load();
  });

  const httpsOk = testResult?.results?.some((r) => r.target === 'HTTPS' && r.ok);
  const aliveCount = items?.filter((p) => p.alive).length || 0;
  const total = items?.length || 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="prompt">
          <span className="p-user">proxy</span>
          <span className="p-at">@</span>
          <span className="p-host">mhfcorp</span>
          <span className="p-sep">:</span>
          <span className="p-path">~/proxy</span>
          <span className="p-end">#</span>
        </div>
        <div className="status">
          <span className="badge badge-idle">proxy pool</span>
          {items && <span className="status-detail"><b>{aliveCount}</b>/{total} alive</span>}
        </div>
      </header>

      <main className="px">
        {/* ---------- new proxy ---------- */}
        <section>
          <div className="px-section-head"><Icon.shield /> new proxy<span className="px-line" /></div>
          <div className="px-card">
            <div className="px-paste">
              <input className="proxy-paste" placeholder="paste endpoint:  user:pass@host:port  |  socks5://user:pass@host:port  |  host:port" onChange={onPaste} />
              <Icon.clipboard />
            </div>
            <div className="px-fields">
              <label>name<input placeholder="auto: proxy N" value={form.name} onChange={(e) => set('name', e.target.value)} /></label>
              <label>host<input placeholder="host" value={form.host} onChange={(e) => set('host', e.target.value)} /></label>
              <label>port<input placeholder="port" type="number" value={form.port} onChange={(e) => set('port', e.target.value)} /></label>
              <label>scheme
                <select value={form.scheme} onChange={(e) => set('scheme', e.target.value)}>
                  <option value="http">http</option>
                  <option value="socks5">socks5</option>
                </select>
              </label>
              <label>username<input placeholder="username (opt)" value={form.username} onChange={(e) => set('username', e.target.value)} /></label>
              <label>password<input placeholder="password (opt)" type="password" value={form.password} onChange={(e) => set('password', e.target.value)} /></label>
              <label className="check"><input type="checkbox" checked={form.active} onChange={(e) => set('active', e.target.checked)} /> active</label>
            </div>
            <div className="px-actions">
              <button className="btn btn-primary" onClick={test} disabled={has('test')}>
                {has('test') && <Spinner />}{has('test') ? 'testing…' : 'test connection'}
              </button>
              <button className="btn" onClick={add} disabled={!httpsOk || has('add')}>
                {has('add') && <Spinner />}<Icon.plus /> add
              </button>
              {testResult && (
                <div className="px-diag">
                  {testResult.results.map((r) => (
                    <span key={r.target} className={`px-chip ${r.ok ? 'ok' : 'bad'}`}>
                      {r.ok ? <Icon.check /> : <Icon.x />}
                      {r.target}
                      <span className="px-ms" title={r.error || ''}>{r.ok ? r.ip : (r.error || 'fail').slice(0, 36)}</span>
                      <span className="px-ms">{r.ms}ms</span>
                    </span>
                  ))}
                  {!httpsOk && <span className="muted">HTTPS must ✓ — the bot needs it</span>}
                </div>
              )}
              {msg && <span className="muted">{msg}</span>}
            </div>
          </div>
        </section>

        {/* ---------- proxy pool ---------- */}
        <section>
          <div className="px-section-head">
            <Icon.plug /> proxy pool<span className="px-line" />
            {items && <span className="px-count">{aliveCount}/{total} alive</span>}
          </div>
          {items && total === 0 ? (
            <div className="px-empty">no proxies yet — add one above.</div>
          ) : items && (
            <div className="px-pool">
              {items.map((p) => (
                <div className="px-proxy" key={p.id}>
                  <div className="px-proxy-head">
                    <span className={`px-dot ${p.alive ? 'alive' : (p.last_checked_at ? 'dead' : '')}`} />
                    <span className="px-proxy-name">{p.name}</span>
                    {p.scheme && <span className="px-tag">{p.scheme}</span>}
                    {!p.active && <span className="px-tag">off</span>}
                  </div>
                  <div className="px-proxy-host">{p.host}:{p.port}{p.username ? ` · ${p.username}` : ''}</div>
                  {p.alive
                    ? <div className="px-proxy-ip">{p.last_ip || '—'}</div>
                    : (p.last_checked_at
                        ? <div className="px-proxy-err" title={p.last_error}>{p.last_error || 'no ip'}</div>
                        : <div className="muted">not checked yet</div>)}
                  <div className="px-proxy-meta">
                    <span className="px-ts">{p.last_checked_at ? fmtJakarta(p.last_checked_at) : '—'}</span>
                    <span className="px-proxy-actions">
                      <button className="btn btn-icon" title="edit" onClick={() => setEditingId(p.id)}>
                        <Icon.pencil />
                      </button>
                      <button className="btn btn-icon" title="check" disabled={has('check-'+p.id)} onClick={() => check(p.id)}>
                        {has('check-'+p.id) ? <Spinner /> : <Icon.refresh />}
                      </button>
                      <button className="btn btn-icon" title={p.active ? 'disable' : 'enable'} disabled={has('toggle-'+p.id)} onClick={() => toggle(p)}>
                        {has('toggle-'+p.id) ? <Spinner /> : <Icon.power />}
                      </button>
                      <button className="btn btn-icon btn-danger" title="delete" disabled={has('del-'+p.id)} onClick={() => del(p)}>
                        {has('del-'+p.id) ? <Spinner /> : <Icon.trash />}
                      </button>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ---------- proxy log ---------- */}
        <section>
          <div className="px-section-head">
            <Icon.refresh /> proxy log<span className="px-line" />
            <button className="btn" disabled={log.length === 0 || has('clearlog')} onClick={() => setConfirmClear(true)}>clear log</button>
          </div>
          <div className="proxy-log">
            {log.length === 0 ? (
              <p className="muted" style={{ padding: '14px 12px' }}>no checks yet — click “check” on a proxy above.</p>
            ) : log.map((l) => (
              <div key={l.id} className={`proxy-log-row ${l.alive ? 'is-ok' : 'is-err'}`}>
                <span className="proxy-log-ts">{fmtJakarta(l.checked_at) || '—'}</span>
                <span className="proxy-log-name">{l.proxy_name || '#' + l.proxy_id}</span>
                <span className={`proxy-log-flag ${l.alive ? 'ok' : 'err'}`}>
                  {l.alive ? 'alive' : 'dead'}{l.scheme ? ` (${l.scheme})` : ''}{l.alive && l.ip ? ` · ${l.ip}` : ''}
                </span>
                <pre className="proxy-log-detail">{l.detail || l.error || (l.alive ? '' : 'no detail')}</pre>
              </div>
            ))}
          </div>
        </section>
      </main>

      {confirmClear && (
        <div className="modal" onClick={() => setConfirmClear(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>clear proxy log</h3>
            <p className="muted" style={{ textTransform: 'none', letterSpacing: 0, fontSize: 13 }}>
              Are you sure you want to clear the proxy log? This permanently deletes all proxy check history and cannot be undone.
            </p>
            <div className="row">
              <button className="btn btn-primary" disabled={has('clearlog')} onClick={() => act('clearlog', async () => {
                await api.clearProxyLog();
                setConfirmClear(false);
                loadLog();
                toast.success('proxy log cleared');
              })}>{has('clearlog') && <Spinner />} clear log</button>
              <button className="btn" onClick={() => setConfirmClear(false)}>cancel</button>
            </div>
          </div>
        </div>
      )}

      {editingId && (
        <ProxyEdit proxyId={editingId}
          onCancel={() => setEditingId(null)}
          onSaved={() => { setEditingId(null); load(); }} />
      )}
    </div>
  );
}

function ProxyEdit({ proxyId, onCancel, onSaved }) {
  const [a, setA] = useState(null);
  const { act, has } = useActions();
  const toast = useToast();
  useEffect(() => {
    api.proxy(proxyId).then(setA).catch((e) => { toast.error(String((e && e.message) || e)); onCancel(); });
  }, [proxyId]);
  const set = (k, v) => setA((p) => ({ ...p, [k]: v }));
  return (
    <div className="modal" onClick={onCancel}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>edit proxy</h3>
        {!a ? <p className="muted">loading…</p> : (
          <>
            <label className="set-field"><span className="set-field-label">Name</span>
              <input value={a.name || ''} onChange={(e) => set('name', e.target.value)} /></label>
            <div className="grid2">
              <label className="set-field"><span className="set-field-label">Host</span>
                <input value={a.host || ''} onChange={(e) => set('host', e.target.value)} /></label>
              <label className="set-field"><span className="set-field-label">Port</span>
                <input type="number" value={a.port || ''} onChange={(e) => set('port', e.target.value)} /></label>
            </div>
            <div className="grid2">
              <label className="set-field"><span className="set-field-label">Scheme</span>
                <select value={a.scheme || 'http'} onChange={(e) => set('scheme', e.target.value)}>
                  <option value="http">http</option>
                  <option value="socks5">socks5</option>
                </select></label>
              <label className="set-field"><span className="set-field-label">Active</span>
                <select value={a.active ? '1' : '0'} onChange={(e) => set('active', e.target.value === '1')}>
                  <option value="1">active</option>
                  <option value="0">disabled</option>
                </select></label>
            </div>
            <label className="set-field"><span className="set-field-label">Username</span>
              <input value={a.username || ''} onChange={(e) => set('username', e.target.value)} /></label>
            <label className="set-field"><span className="set-field-label">Password</span>
              <input value={a.password || ''} onChange={(e) => set('password', e.target.value)} /></label>
            <div className="row">
              <button className="btn btn-primary" disabled={has('saveproxy')} onClick={() => act('saveproxy', async () => {
                await api.updateProxy(proxyId, { name: a.name, host: a.host, port: +a.port, scheme: a.scheme, username: a.username, password: a.password, active: a.active });
                toast.success('proxy updated');
                onSaved();
              })}>{has('saveproxy') && <Spinner />} save</button>
              <button className="btn" onClick={onCancel}>cancel</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
