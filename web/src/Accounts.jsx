import { Fragment, useEffect, useRef, useState } from 'react';
import { api } from './api.js';

// Format an ISO timestamp as Jakarta date+time (GMT+7), e.g. "06 Jul 14.30".
export const fmtJakarta = (iso) => (iso
  ? new Date(iso).toLocaleString('id-ID', {
      timeZone: 'Asia/Jakarta',
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit',
      hour12: false,
    })
  : '');

const empty = {
  name: '', username: '', auth_token: '', ct0: '', keywords: '',
  mode: 'search', daily_posts: 0, daily_likes: 5, daily_retweets: 2, daily_replies: 0,
  like_probability: 0.6, retweet_probability: 0.4, active: true, proxy_id: null,
};

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [today, setToday] = useState({});
  const [editing, setEditing] = useState(null);
  const [logsId, setLogsId] = useState(null);
  const [schedId, setSchedId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [err, setErr] = useState('');

  const load = () => {
    api.accounts().then((a) => {
      setAccounts(a);
      a.forEach((acc) => api.today(acc.id).then((t) => setToday((p) => ({ ...p, [acc.id]: t }))).catch(() => {}));
    }).catch((e) => setErr(String(e)));
  };
  useEffect(load, []);

  const save = async (a) => {
    const payload = {
      ...a,
      keywords: typeof a.keywords === 'string'
        ? a.keywords.split(',').map((s) => s.trim()).filter(Boolean)
        : a.keywords,
      daily_posts: +a.daily_posts, daily_likes: +a.daily_likes, daily_retweets: +a.daily_retweets,
      daily_replies: +a.daily_replies,
      like_probability: +a.like_probability, retweet_probability: +a.retweet_probability,
      proxy_id: a.proxy_id ? +a.proxy_id : null,
    };
    try {
      if (a.id) await api.updateAccount(a.id, payload);
      else await api.createAccount(payload);
      setEditing(null);
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div>
      <div className="row">
        <button onClick={() => setEditing({ ...empty })}>add account</button>
        {err && <span className="err">{err}</span>}
      </div>
      <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Name</th><th>@</th><th>Mode</th><th>Active</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {accounts.map((a) => {
            const open = expandedId === a.id;
            const done = (today[a.id] && today[a.id].done) || {posts:0, likes:0, retweets:0, replies:0};
            const meters = [
              { label: 'Posts', done: done.posts, quota: a.daily_posts ?? 0 },
              { label: 'Likes', done: done.likes, quota: a.daily_likes ?? 0 },
              { label: 'Retweets', done: done.retweets, quota: a.daily_retweets ?? 0 },
              { label: 'Replies', done: done.replies ?? 0, quota: a.daily_replies ?? 0 },
            ];
            return (
              <Fragment key={a.id}>
                <tr className={`acct-row${open ? ' acct-open' : ''}`} onClick={() => setExpandedId(open ? null : a.id)}>
                  <td><span className="chev">{open ? '▾' : '▸'}</span>{a.name}</td>
                  <td>{a.username}</td>
                  <td>{a.mode}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={a.active} onChange={async (e) => { await api.updateAccount(a.id, { active: e.target.checked }); load(); }} />
                  </td>
                  <td className="actions" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => api.runNow(a.id).then((r) => { if (!r.queued) alert(r.msg || 'nothing to run'); setTimeout(load, 800); })}>run now</button>
                    <button onClick={() => setSchedId(a.id)}>schedule list</button>
                    <button onClick={() => api.replan(a.id).then((r) => { alert((r.remaining || 0) + ' actions remaining '); load(); })}>check</button>
                    <button onClick={() => setLogsId(a.id)}>logs</button>
                    <button onClick={() => setEditing({ ...a, keywords: (a.keywords || []).join(', ') })}>edit</button>
                    <button onClick={() => { if (confirm('Delete ' + a.name + '?')) api.deleteAccount(a.id).then(load); }}>del</button>
                  </td>
                </tr>
                {open && (
                  <tr className="acct-detail-row">
                    <td colSpan={5}>
                      <div className="acct-detail">
                        <h4>done today / quota</h4>
                        <div className="meters">
                          {meters.map((m) => {
                            const pct = m.quota > 0 ? Math.min(100, (m.done / m.quota) * 100) : 0;
                            const complete = m.quota > 0 && m.done >= m.quota;
                            return (
                              <div key={m.label} className={`meter${complete ? ' done' : ''}`}>
                                <span className="meter-label">{m.label}</span>
                                <span className="meter-track"><span className="meter-fill" style={{ width: pct + '%' }} /></span>
                                <span className="meter-val">{m.done} / {m.quota}</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      </div>
      <ActivityFeed />
      {editing && <AccountForm value={editing} onCancel={() => setEditing(null)} onSave={save} />}
      {logsId && <RunLogs accountId={logsId} onClose={() => setLogsId(null)} />}
      {schedId && <ScheduleView accountId={schedId} onClose={() => setSchedId(null)} />}
    </div>
  );
}

function Field({ label, children }) {
  return <label>{label}{children}</label>;
}

function AccountForm({ value, onCancel, onSave }) {
  const [a, setA] = useState(value);
  const [proxies, setProxies] = useState([]);
  const set = (k, v) => setA((p) => ({ ...p, [k]: v }));
  const num = (k) => (e) => set(k, e.target.value);
  useEffect(() => { api.proxies().then(setProxies).catch(() => {}); }, []);
  return (
    <div className="modal">
      <div className="modal-card">
        <h3>{value.id ? 'Edit account' : 'Add account'}</h3>
        <Field label="Name"><input value={a.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Username (no @)"><input value={a.username} onChange={(e) => set('username', e.target.value)} /></Field>
        <Field label={value.id ? 'auth_token (blank = keep)' : 'auth_token'}><input type="password" value={a.auth_token || ''} onChange={(e) => set('auth_token', e.target.value)} /></Field>
        <Field label={value.id ? 'ct0 (blank = keep)' : 'ct0'}><input type="password" value={a.ct0 || ''} onChange={(e) => set('ct0', e.target.value)} /></Field>
        <Field label="Keywords (comma-separated)"><input value={a.keywords} onChange={(e) => set('keywords', e.target.value)} /></Field>
        <Field label="Mode">
          <select value={a.mode} onChange={(e) => set('mode', e.target.value)}>
            <option value="search">search</option>
            <option value="timeline">timeline</option>
          </select>
        </Field>
        <div className="grid4">
          <Field label="Daily posts"><input type="number" value={a.daily_posts} onChange={num('daily_posts')} /></Field>
          <Field label="Daily likes"><input type="number" value={a.daily_likes} onChange={num('daily_likes')} /></Field>
          <Field label="Daily retweets"><input type="number" value={a.daily_retweets} onChange={num('daily_retweets')} /></Field>
          <Field label="Daily replies"><input type="number" value={a.daily_replies} onChange={num('daily_replies')} /></Field>
        </div>
        <div className="grid2">
          <Field label="Like prob"><input value={a.like_probability} onChange={num('like_probability')} /></Field>
          <Field label="Retweet prob"><input value={a.retweet_probability} onChange={num('retweet_probability')} /></Field>
        </div>
        <Field label="Proxy">
          <select value={a.proxy_id ?? ''} onChange={(e) => set('proxy_id', e.target.value || null)}>
            <option value="">no proxy (local IP)</option>
            {proxies.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.host}:{p.port}</option>)}
          </select>
        </Field>
        <label className="check"><input type="checkbox" checked={a.active} onChange={(e) => set('active', e.target.checked)} /> Active</label>
        <div className="row">
          <button onClick={() => onSave(a)}>{value.id ? 'save' : 'create'}</button>
          <button onClick={onCancel}>cancel</button>
        </div>
      </div>
    </div>
  );
}

function RunLogs({ accountId, onClose }) {
  const [runs, setRuns] = useState([]);
  const [actions, setActions] = useState([]);
  useEffect(() => {
    const load = () => { api.runs(accountId).then(setRuns); api.actions(accountId).then(setActions); };
    load();
    const i = setInterval(load, 3000);
    return () => clearInterval(i);
  }, [accountId]);
  return (
    <div className="modal">
      <div className="modal-card wide">
        <div className="row"><h3>account #{accountId} · runs</h3><button onClick={onClose}>close</button></div>
        <table>
          <thead><tr><th>When</th><th>Trigger</th><th>Status</th><th>Counts</th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>{r.started_at}</td>
                <td>{r.trigger}</td>
                <td className={r.status === 'error' ? 'err' : ''}>{r.status}</td>
                <td>{JSON.stringify(r.counts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h4>actions.jsonl — tail</h4>
        <pre className="jsonl">{actions.map((x) => JSON.stringify(x)).join('\n')}</pre>
      </div>
    </div>
  );
}

function ScheduleView({ accountId, onClose }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const load = () => api.schedule(accountId).then(setItems).catch(() => {});
    load();
    const i = setInterval(load, 4000);
    return () => clearInterval(i);
  }, [accountId]);
  const fmt = fmtJakarta;
  return (
    <div className="modal">
      <div className="modal-card">
        <div className="row"><h3>account #{accountId} · today's plan</h3><button onClick={onClose}>close</button></div>
        {items.length === 0 ? (
          <p className="muted">No actions yet. The scheduler starts the chain on its next tick (if the day window is open and quota remains), or click <b>Replan</b>.</p>
        ) : (
          <table>
            <thead><tr><th>Time</th><th>Action</th><th>Status</th></tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>{fmt(it.run_at)}</td>
                  <td>{it.action_type}</td>
                  <td className={it.status === 'error' ? 'err' : (it.status === 'done' ? 'ok' : '')}>{it.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="muted">One next action is scheduled at a time; each run sets the following action's randomized gap (spread across the day window). Done actions are counted toward today's quota.</p>
      </div>
    </div>
  );
}

// ---- live activity feed (global, newest runs across all accounts) ----

const ACT_VERB = { post: 'posted', like: 'liked', retweet: 'retweeted', reply: 'replied' };

function describeCounts(counts) {
  const parts = [];
  for (const [k, v] of Object.entries(counts || {})) {
    if (!v || k === 'error' || k === 'status') continue;
    const verb = ACT_VERB[k] || k;
    parts.push(v > 1 ? `${verb} ×${v}` : verb);
  }
  return parts.join(', ') || 'ran';
}

const STATUS_GLYPH = { ok: '✓', error: '✗', skipped: '◌' };

function ActivityFeed() {
  const [items, setItems] = useState([]);
  const [freshIds, setFreshIds] = useState(() => new Set());
  const prevTop = useRef(0);

  useEffect(() => {
    const load = () => api.activity().then((rows) => {
      const top = rows.length ? rows[0].id : 0;
      // only genuinely-new rows (after the first load) slide in
      const fresh = prevTop.current > 0
        ? new Set(rows.filter((r) => r.id > prevTop.current).map((r) => r.id))
        : new Set();
      prevTop.current = top;
      setItems(rows);
      setFreshIds(fresh);
    }).catch(() => {});
    load();
    const i = setInterval(load, 3000);
    return () => clearInterval(i);
  }, []);

  const fmt = fmtJakarta;

  return (
    <section className="feed">
      <div className="feed-head">
        <h4>// activity</h4>
        <span className="live"><span className="live-dot" /> live</span>
      </div>
      {items.length === 0 ? (
        <p className="muted">awaiting activity…</p>
      ) : (
        <div className="feed-list">
          {items.map((r) => (
            <div key={r.id} className={`feed-row ${r.status} ${freshIds.has(r.id) ? 'fresh' : ''}`}>
              <span className="feed-time">{fmt(r.started_at)}</span>
              <span className="feed-acct">
                {r.account_name || '#' + r.account_id}
                {r.exit_ip && <span className="feed-ip" title="exit IP used"> · {r.exit_ip}</span>}
              </span>
              <span className="feed-act">{describeCounts(r.counts)}</span>
              <span className="feed-trig">{r.trigger}</span>
              <span className="feed-status">{STATUS_GLYPH[r.status] || '·'}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
