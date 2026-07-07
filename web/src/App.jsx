import { useEffect, useState } from 'react';
import { api } from './api';
import Accounts from './Accounts.jsx';
import Settings from './Settings.jsx';
import Youtube from './Youtube.jsx';
import Proxy from './Proxy.jsx';

export default function App() {
  const [authed, setAuthed] = useState(null); // null=unknown, true, false
  const [tab, setTab] = useState('accounts');
  const [app, setApp] = useState('xbot');
  const [status, setStatus] = useState(null);
  const [sideOpen, setSideOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const a = await api.auth();
        if (!a.auth_required) { if (alive) setAuthed(true); return; }
        try { await api.status(); if (alive) setAuthed(true); }
        catch { if (alive) setAuthed(false); }
      } catch { if (alive) setAuthed(true); }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (authed !== true || app !== 'xbot') return;
    const tick = () => api.status().then(setStatus).catch(() => {});
    tick();
    const i = setInterval(tick, 3000);
    return () => clearInterval(i);
  }, [authed, app]);

  if (authed === null) return <div className="app"><p className="muted">initializing…</p></div>;
  if (authed === false) return <Login onOk={() => setAuthed(true)} />;

  const item = MENU.find((m) => m.id === app);
  return (
    <div className={`shell${sideOpen ? ' side-open' : ''}`}>
      <button className="menu-toggle" onClick={() => setSideOpen((o) => !o)} aria-label="toggle menu">
        {sideOpen ? '✕' : '☰'}
      </button>
      <Sidebar app={app} setApp={setApp} onLogout={() => api.logout().finally(() => setAuthed(false))} />
      <div className="workspace">
        {app === 'xbot' ? (
          <div className="app">
            <header className="topbar">
              <div className="prompt">
                <span className="p-user">xbot</span>
                <span className="p-at">@</span>
                <span className="p-host">mhfcorp</span>
                <span className="p-sep">:</span>
                <span className="p-path">~/xbot</span>
                <span className="p-end">#</span>
              </div>
              <div className="status">
                {status ? (
                  <>
                    <span className={`badge ${status.running ? 'badge-run' : 'badge-idle'}`}>
                      {status.running ? 'RUN' : 'IDLE'}
                    </span>
                    <span className="status-detail">
                      {status.running
                        ? <><b>{status.current.name}</b> · {status.current.trigger}</>
                        : 'awaiting tick'}
                    </span>
                    <span className="status-detail">sched <b>{status.schedule_active ? 'on' : 'off'}</b></span>
                    <span className="status-detail">today <b>{status.today_done}/{status.today_done + status.today_pending}</b></span>
                    <span className="status-detail">queue <b>{status.queue_depth}</b></span>
                  </>
                ) : (
                  <span className="muted">connecting…</span>
                )}
              </div>
              <nav>
                <button className={tab === 'accounts' ? 'tab active' : 'tab'} onClick={() => setTab('accounts')}>accounts</button>
                <button className={tab === 'settings' ? 'tab active' : 'tab'} onClick={() => setTab('settings')}>settings</button>
              </nav>
            </header>
            <main key={tab} className="tab-pane">{tab === 'accounts' ? <Accounts /> : <Settings />}</main>
          </div>
        ) : app === 'proxy' ? (
          <Proxy />
        ) : app === 'youtube' ? (
          <Youtube />
        ) : (
          <ComingSoon item={item} />
        )}
      </div>
    </div>
  );
}

function Login({ onOk }) {
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    try { await api.login(pw); onOk(); }
    catch { setErr('access denied'); }
  };
  return (
    <div className="app">
      <form className="login-card" onSubmit={submit}>
        <h1>xbot // auth</h1>
        <input type="password" autoFocus value={pw} placeholder="password"
               onChange={(e) => setPw(e.target.value)} />
        <button type="submit">authenticate</button>
        {err && <span className="err">{err}</span>}
      </form>
    </div>
  );
}

// ---- left sidebar: app switcher --------------------------------------

const MENU = [
  { id: 'xbot', label: 'XBOT', glyph: '✕', soon: false },
  { id: 'proxy', label: 'Proxy', glyph: '⇄', soon: false },
  { id: 'youtube', label: 'Youtube Automation', glyph: '▶', soon: false },
  { id: 'thread', label: 'Thread Automation', glyph: '≡', soon: true },
  { id: 'instagram', label: 'Instagram Content Generator', glyph: '◉', soon: true },
];

function Sidebar({ app, setApp, onLogout }) {
  return (
    <aside className="sidebar">
      <div className="side-brand"><span className="side-brand-mark">◆</span>mhfcorp</div>
      <nav className="side-menu">
        {MENU.map((m) => (
          <button
            key={m.id}
            className={`side-item${app === m.id ? ' active' : ''}`}
            onClick={() => setApp(m.id)}
          >
            <span className="side-glyph">{m.glyph}</span>
            <span className="side-label">{m.label}</span>
            {m.soon && <span className="side-soon">soon</span>}
          </button>
        ))}
      </nav>
      <div className="side-foot">
        <button className="side-item" onClick={onLogout}>
          <span className="side-glyph">⏻</span>
          <span className="side-label">logout</span>
        </button>
      </div>
    </aside>
  );
}

function ComingSoon({ item }) {
  return (
    <div className="soon">
      <div className="soon-glyph">{item.glyph}</div>
      <h1>{item.label}</h1>
      <p className="soon-tag">coming soon</p>
      <p className="muted">this module isn't built yet — check back later.</p>
    </div>
  );
}
