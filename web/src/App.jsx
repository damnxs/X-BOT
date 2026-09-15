import { Fragment, useEffect, useState } from 'react';
import { api } from './api';
import Accounts from './Accounts.jsx';
import Settings from './Settings.jsx';
import AutoReply from './AutoReply.jsx';
import Proxy from './Proxy.jsx';
import Docs from './Docs.jsx';
import Raider from './Raider.jsx';
import DryRun from './DryRun.jsx';

// monoline icons — stroke=currentColor, sized by their container's CSS
const S = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' };
const svg = (children) => (p) => (<svg {...S} {...p}>{children}</svg>);
const I = {
  brand:    svg(<path d="M12 2 3 12l9 10 9-10z"/>),
  grid:     svg(<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/></>),
  plug:     svg(<><path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0V8zM12 16v6"/></>),
  reply:    svg(<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>),
  threads:  svg(<><circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.5 7.6"/></>),
  instagram: svg(<><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/></>),
  logout:   svg(<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></>),
  chevron:  svg(<path d="m15 18-6-6 6-6"/>),
  book:     svg(<><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></>),
  target:   svg(<><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2" fill="currentColor" stroke="none"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></>),
  bug:      svg(<><path d="M8 2l1.5 2M16 2l-1.5 2"/><rect x="6" y="6" width="12" height="12" rx="6"/><path d="M3 12h3M18 12h3M5 6l2 2M19 6l-2 2M5 18l2-2M19 18l-2-2M12 18v3"/></>),
};

export default function App() {
  const [authed, setAuthed] = useState(null); // null=unknown, true, false
  const [tab, setTab] = useState('accounts');
  const [app, setApp] = useState('xbot');
  const [status, setStatus] = useState(null);
  const [sideOpen, setSideOpen] = useState(false);   // mobile drawer
  const [collapsed, setCollapsed] = useState(false); // desktop rail

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

  const go = (id) => { setApp(id); setSideOpen(false); }; // navigate + close mobile drawer
  const item = MENU.find((m) => m.id === app);
  return (
    <div className={`shell${sideOpen ? ' side-open' : ''}${collapsed ? ' collapsed' : ''}`}>
      <div className="side-backdrop" onClick={() => setSideOpen(false)} />
      <button className="menu-toggle" onClick={() => setSideOpen((o) => !o)} aria-label="toggle menu">
        {sideOpen ? '✕' : '☰'}
      </button>
      <Sidebar app={app} go={go} collapsed={collapsed} setCollapsed={setCollapsed}
               onLogout={() => api.logout().finally(() => setAuthed(false))} />
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
            <main key={tab} className="tab-pane">{tab === 'accounts' ? <Accounts status={status} /> : <Settings />}</main>
          </div>
        ) : app === 'proxy' ? (
          <Proxy />
        ) : app === 'raider' ? (
          <Raider />
        ) : app === 'dryrun' ? (
          <DryRun />
        ) : app === 'autoreply' ? (
          <AutoReply />
        ) : app === 'docs' ? (
          <Docs />
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
  { id: 'xbot', label: 'Warm Up', icon: I.grid, glyph: '✕', group: 'workspace' },
  { id: 'proxy', label: 'Proxy', icon: I.plug, glyph: '⇄', group: 'workspace' },
  { id: 'raider', label: 'Raider', icon: I.target, glyph: '◎', group: 'workspace' },
  { id: 'dryrun', label: 'Dry Run', icon: I.bug, glyph: '◆', group: 'workspace' },
  { id: 'autoreply', label: 'Twitter Auto Reply', icon: I.reply, glyph: '✕', group: 'workspace' },
  { id: 'thread', label: 'Thread Automation', icon: I.threads, glyph: '≡', group: 'soon', soon: true },
  { id: 'instagram', label: 'Instagram Content Generator', icon: I.instagram, glyph: '◉', group: 'soon', soon: true },
  { id: 'docs', label: 'Documentation', icon: I.book, glyph: '?', group: 'help' },
];

const MENU_GROUPS = [
  { label: 'workspace', items: MENU.filter((m) => m.group === 'workspace') },
  { label: 'coming soon', items: MENU.filter((m) => m.group === 'soon') },
  { label: 'help', items: MENU.filter((m) => m.group === 'help') },
].filter((g) => g.items.length);

function SideItem({ m, active, onClick }) {
  return (
    <button className={`side-item${active ? ' active' : ''}`} onClick={onClick} data-tip={m.label}>
      <span className="side-icon">{m.icon ? <m.icon /> : null}</span>
      <span className="side-label">{m.label}</span>
      {m.soon && <span className="side-soon">soon</span>}
    </button>
  );
}

function Sidebar({ app, go, collapsed, setCollapsed, onLogout }) {
  return (
    <aside className="sidebar">
      <div className="side-head">
        <div className="side-brand">
          <span className="side-brand-mark"><I.brand /></span>
          <span className="side-brand-name">mhfcorp</span>
        </div>
        <button className="side-collapse" onClick={() => setCollapsed((c) => !c)}
                aria-label="toggle sidebar" title={collapsed ? 'Expand' : 'Collapse'}>
          <I.chevron />
        </button>
      </div>

      <nav className="side-menu">
        {MENU_GROUPS.map((g) => (
          <Fragment key={g.label}>
            <div className="side-group-label">{g.label}</div>
            {g.items.map((m) => (
              <SideItem key={m.id} m={m} active={app === m.id} onClick={() => go(m.id)} />
            ))}
          </Fragment>
        ))}
      </nav>

      <div className="side-foot">
        <button className="side-item" onClick={onLogout} data-tip="logout">
          <span className="side-icon"><I.logout /></span>
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
