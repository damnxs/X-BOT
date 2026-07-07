import { useEffect, useState } from 'react';
import { api } from './api.js';
import { fmtJakarta } from './Accounts.jsx';

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

export default function Proxy() {
  const [items, setItems] = useState(null);
  const [log, setLog] = useState([]);
  const [form, setForm] = useState(empty);
  const [checking, setChecking] = useState(() => new Set());
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [msg, setMsg] = useState('');

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
  const test = async (e) => {
    e.preventDefault();
    if (!form.host || !form.port) { setMsg('host, port required'); return; }
    setTesting(true); setMsg('');
    try {
      const r = await api.testProxy({ host: form.host, port: +form.port, scheme: form.scheme, username: form.username, password: form.password });
      setTestResult(r);
    } catch (err) { setMsg(String(err)); }
    setTesting(false);
  };
  const add = async (e) => {
    e.preventDefault();
    if (!testResult?.results?.some((r) => r.target === 'HTTPS' && r.ok)) {
      setMsg('HTTPS must pass before adding — the bot needs it'); return;
    }
    try {
      await api.createProxy({ ...form, port: +form.port });
      setForm(empty); setTestResult(null);
      setMsg('added');
      load();
    } catch (err) { setMsg(String(err)); }
  };
  const check = async (id) => {
    setChecking((s) => new Set(s).add(id));
    try { await api.checkProxy(id); } catch (err) { setMsg(String(err)); }
    setChecking((s) => { const n = new Set(s); n.delete(id); return n; });
    load(); loadLog();
  };
  const toggle = async (p) => { await api.updateProxy(p.id, { active: !p.active }); load(); };
  const del = async (p) => { if (confirm('Delete ' + p.name + '?')) { await api.deleteProxy(p.id); load(); } };
  const httpsOk = testResult?.results?.some((r) => r.target === 'HTTPS' && r.ok);

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
        <div className="status"><span className="badge badge-idle">proxy pool</span></div>
      </header>

      <main>
        <input
          className="proxy-paste"
          placeholder="paste endpoint:  user:pass:host:port  |  socks5://user:pass@host:port  |  host:port"
          onChange={onPaste}
        />
        <form className="row proxy-add" onSubmit={(e) => e.preventDefault()}>
          <input placeholder="name (auto: proxy N)" value={form.name} onChange={(e) => set('name', e.target.value)} />
          <input placeholder="host" value={form.host} onChange={(e) => set('host', e.target.value)} />
          <input placeholder="port" type="number" value={form.port} onChange={(e) => set('port', e.target.value)} />
          <select value={form.scheme} onChange={(e) => set('scheme', e.target.value)} title="proxy scheme">
            <option value="http">http</option>
            <option value="socks5">socks5</option>
          </select>
          <input placeholder="username (opt)" value={form.username} onChange={(e) => set('username', e.target.value)} />
          <input placeholder="password (opt)" type="password" value={form.password} onChange={(e) => set('password', e.target.value)} />
          <label className="check"><input type="checkbox" checked={form.active} onChange={(e) => set('active', e.target.checked)} /> active</label>
          <button type="button" onClick={test} disabled={testing}>{testing ? 'testing…' : 'test connection'}</button>
          <button type="button" onClick={add} disabled={!httpsOk}>add</button>
          {testResult && (
            <span className="proxy-diag">
              {testResult.results.map((r) => (
                <span key={r.target} className={r.ok ? 'ok' : 'err'}>
                  {r.target}: {r.ok ? `✓ ${r.ip}` : `✗ ${(r.error || 'fail').slice(0, 55)}`} · {r.ms}ms
                </span>
              ))}
              {!httpsOk && <span className="muted">HTTPS must ✓ for the X bot (it's HTTPS)</span>}
            </span>
          )}
          {msg && <span className="muted">{msg}</span>}
        </form>

        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name</th><th>Host:Port</th><th>Status</th><th>Last IP</th><th>Checked</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {items && items.length === 0 ? (
                <tr><td colSpan={6} className="empty-cell">no proxies yet — add one above.</td></tr>
              ) : items && items.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}{p.active ? '' : <span className="muted"> (off)</span>}</td>
                  <td>{p.host}:{p.port}{p.username ? ` · ${p.username}` : ''}</td>
                  <td>
                    {p.last_checked_at
                      ? (p.alive
                          ? <span className="ok">● alive{p.scheme ? ` (${p.scheme})` : ''}</span>
                          : <span className="err">● dead</span>)
                      : <span className="muted">—</span>}
                  </td>
                  <td>{p.alive
                    ? (p.last_ip || '—')
                    : <span className="muted" title={p.last_error}>{p.last_error || 'no ip'}</span>}</td>
                  <td className="muted">{p.last_checked_at || '—'}</td>
                  <td className="actions">
                    <button disabled={checking.has(p.id)} onClick={() => check(p.id)}>
                      {checking.has(p.id) ? 'checking…' : 'check'}
                    </button>
                    <button onClick={() => toggle(p)}>{p.active ? 'disable' : 'enable'}</button>
                    <button onClick={() => del(p)}>del</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h4 className="proxy-log-head">// proxy log</h4>
        <div className="proxy-log">
          {log.length === 0 ? (
            <p className="muted">no checks yet — click “check” on a proxy above.</p>
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
      </main>
    </div>
  );
}
