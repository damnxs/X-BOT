import { Fragment, useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { useActions, useToast, Spinner } from './toast.jsx';

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
  pop_min_likes: 0, pop_min_replies: 0, pop_tab: 'live',
};

const MODE_LABEL = { search: 'search', timeline: 'timeline', search_popularity: 'popular' };

// monoline icons — stroke=currentColor, sized by container CSS
const S = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' };
const svg = (children) => (p) => (<svg {...S} {...p}>{children}</svg>);
const I = {
  plus:     svg(<path d="M12 5v14M5 12h14"/>),
  users:    svg(<><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></>),
  activity: svg(<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>),
  bolt:     svg(<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>),
  clock:    svg(<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>),
  play:     svg(<path d="M6 4l14 8-14 8z"/>),
  calendar: svg(<><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M3 10h18M8 2v4M16 2v4"/></>),
  refresh:  svg(<><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></>),
  list:     svg(<><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="0.6" fill="currentColor" stroke="none"/><circle cx="3.5" cy="12" r="0.6" fill="currentColor" stroke="none"/><circle cx="3.5" cy="18" r="0.6" fill="currentColor" stroke="none"/></>),
  pencil:   svg(<><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></>),
  trash:    svg(<><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></>),
};

export default function Accounts({ status }) {
  const [accounts, setAccounts] = useState([]);
  const [today, setToday] = useState({});
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(null);
  const [logsId, setLogsId] = useState(null);
  const [schedId, setSchedId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [err, setErr] = useState('');
  const { act, has } = useActions();
  const toast = useToast();

  const load = () => {
    api.accounts().then((a) => {
      setAccounts(a);
      setLoaded(true);
      a.forEach((acc) => api.today(acc.id).then((t) => setToday((p) => ({ ...p, [acc.id]: t }))).catch(() => {}));
    }).catch((e) => { setErr(String(e)); setLoaded(true); });
  };
  useEffect(load, []);

  // throws on validation/API error so the caller's useActions can toast it.
  const save = async (a) => {
    if (a.mode === 'search_popularity' && !+a.pop_min_likes) {
      throw new Error('“Search by popularity” needs a min-likes threshold.');
    }
    const payload = {
      ...a,
      keywords: typeof a.keywords === 'string'
        ? a.keywords.split(',').map((s) => s.trim()).filter(Boolean)
        : a.keywords,
      daily_posts: +a.daily_posts, daily_likes: +a.daily_likes, daily_retweets: +a.daily_retweets,
      daily_replies: +a.daily_replies,
      like_probability: +a.like_probability, retweet_probability: +a.retweet_probability,
      proxy_id: a.proxy_id ? +a.proxy_id : null,
      pop_min_likes: +a.pop_min_likes || 0,
      pop_min_replies: +a.pop_min_replies || 0,
      pop_tab: a.pop_tab || 'live',
    };
    if (a.id) await api.updateAccount(a.id, payload);
    else await api.createAccount(payload);
    setEditing(null);
    load();
  };

  // ---- aggregates for stat cards ----
  const total = accounts.length;
  const active = accounts.filter((a) => a.active).length;
  const doneOf = (a) => { const d = (today[a.id] && today[a.id].done) || {}; return (d.posts||0)+(d.likes||0)+(d.retweets||0)+(d.replies||0); };
  const quotaOf = (a) => (a.daily_posts??0)+(a.daily_likes??0)+(a.daily_retweets??0)+(a.daily_replies??0);
  const actionsDone = accounts.reduce((s, a) => s + doneOf(a), 0);
  const actionsQuota = accounts.reduce((s, a) => s + quotaOf(a), 0);
  const completionPct = actionsQuota ? Math.min(100, Math.round(actionsDone / actionsQuota * 100)) : 0;

  // today's scheduled plan (done / total) + live scheduler sub
  const planDone = status?.today_done ?? 0;
  const planPending = status?.today_pending ?? 0;
  const planTotal = planDone + planPending;
  const planPct = planTotal ? Math.min(100, Math.round(planDone / planTotal * 100)) : 0;
  const schedSub = status?.running
    ? `${status.current.name} · ${status.current.trigger}`
    : (status ? (status.schedule_active
        ? `awaiting tick${status.queue_depth ? ' · ' + status.queue_depth + ' queued' : ''}`
        : 'paused') : '—');

  return (
    <div className="dash">
      <header className="dash-head">
        <div className="dash-head-text">
          <h1 className="dash-title">Dashboard</h1>
          <p className="dash-sub">X automation overview · {active} of {total} accounts active</p>
        </div>
        <button className="tab add-tab" onClick={() => setEditing({ ...empty })}>
          <I.plus /> add account
        </button>
      </header>

      {!loaded ? (
        <div className="stat-grid">
          {[0, 1, 2, 3].map((i) => (
            <div className="stat-card skel-card" key={i}>
              <div className="skel skel-line w40" />
              <div className="skel skel-line w60" />
            </div>
          ))}
        </div>
      ) : (
        <>
        <div className="stat-grid">
          <StatCard label="Accounts"
                    tip="Accounts currently active — the scheduler runs actions for these."
                    value={active} sub={`of ${total} total`} />
          <StatCard label="Actions today"
                    tip="Actions performed today vs the total daily quota across all accounts."
                    value={actionsDone} sub={`of ${actionsQuota} quota`} bar={completionPct} />
          <StatCard label="Scheduler"
                    tip="Live scheduler state and the action currently running. 'queued' = actions waiting in the worker queue."
                    value={status?.running ? 'Running' : 'Idle'}
                    sub={schedSub}
                    tone={status?.running ? 'run' : 'idle'} />
          <StatCard label="Today's plan"
                    tip={`Scheduled actions completed today — ${planDone} of ${planTotal} planned have run.`}
                    value={status ? `${planDone}/${planTotal}` : '—'}
                    sub={status ? `${planPending} pending` : ''}
                    bar={planPct} />
        </div>
        <Banner />
        </>
      )}

      <section className="px-card dash-card">
        <div className="px-section-head">
          <I.users /> accounts<span className="px-line" />
          <span className="px-count">{total}</span>
        </div>
        <div className="table-wrap">
          <table className="dsh-table">
            <thead>
              <tr><th>Account</th><th>Mode</th><th>Today</th><th>Status</th><th className="ta-right">Actions</th></tr>
            </thead>
            <tbody>
              {!loaded ? (
                <tr><td colSpan={5} className="muted">loading…</td></tr>
              ) : accounts.length === 0 ? (
                <tr><td colSpan={5} className="dash-empty">no accounts yet — click “add account” to get started.</td></tr>
              ) : accounts.map((a) => {
                const open = expandedId === a.id;
                const d = (today[a.id] && today[a.id].done) || { posts:0, likes:0, retweets:0, replies:0 };
                const q = quotaOf(a);
                const dTotal = doneOf(a);
                const pct = q ? Math.min(100, (dTotal / q) * 100) : 0;
                const meters = [
                  { label: 'Posts', done: d.posts, quota: a.daily_posts ?? 0 },
                  { label: 'Likes', done: d.likes, quota: a.daily_likes ?? 0 },
                  { label: 'Retweets', done: d.retweets, quota: a.daily_retweets ?? 0 },
                  { label: 'Replies', done: d.replies ?? 0, quota: a.daily_replies ?? 0 },
                ];
                return (
                  <Fragment key={a.id}>
                    <tr className={`acct-row${open ? ' acct-open' : ''}`} onClick={() => setExpandedId(open ? null : a.id)}>
                      <td>
                        <div className="acct-name-cell">
                          <span className="chev">{open ? '▾' : '▸'}</span>
                          <span className="acct-name">{a.name}</span>
                          <span className="acct-handle">@{a.username}</span>
                        </div>
                      </td>
                      <td><span className="tag" title={a.mode}>{MODE_LABEL[a.mode] || a.mode}</span></td>
                      <td>
                        <div className="mini-progress">
                          <span className="mini-bar"><span style={{ width: pct + '%' }} /></span>
                          <span className="mini-val">{dTotal}/{q}</span>
                        </div>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <label className="switch" title={a.active ? 'active' : 'paused'}>
                          <input type="checkbox" checked={a.active} disabled={has('toggle-'+a.id)} onChange={() => act('toggle-'+a.id, async () => { await api.updateAccount(a.id, { active: !a.active }); load(); })} />
                          <span className="switch-track" />
                        </label>
                      </td>
                      <td className="cell-actions" onClick={(e) => e.stopPropagation()}>
                        <button className="btn btn-icon" title="run now" disabled={has('run-'+a.id)} onClick={() => act('run-'+a.id, async () => {
                          const r = await api.runNow(a.id);
                          if (r.queued) { toast.success('run queued'); setTimeout(load, 800); }
                          else toast.info(r.msg || 'nothing to run');
                        })}>{has('run-'+a.id) ? <Spinner /> : <I.play />}</button>
                        <button className="btn btn-icon" title="schedule list" onClick={() => setSchedId(a.id)}><I.calendar /></button>
                        <button className="btn btn-icon" title="replan" disabled={has('replan-'+a.id)} onClick={() => act('replan-'+a.id, async () => {
                          const r = await api.replan(a.id);
                          toast.success(`${r.remaining || 0} actions queued`); load();
                        })}>{has('replan-'+a.id) ? <Spinner /> : <I.refresh />}</button>
                        <button className="btn btn-icon" title="logs" onClick={() => setLogsId(a.id)}><I.list /></button>
                        <button className="btn btn-icon" title="edit" onClick={() => setEditing({ ...a, keywords: (a.keywords || []).join(', ') })}><I.pencil /></button>
                        <button className="btn btn-icon btn-danger" title="delete" disabled={has('del-'+a.id)} onClick={() => act('del-'+a.id, async () => {
                          if (!confirm('Delete ' + a.name + '?')) return;
                          await api.deleteAccount(a.id); toast.success('account deleted'); load();
                        })}>{has('del-'+a.id) ? <Spinner /> : <I.trash />}</button>
                      </td>
                    </tr>
                    {open && (
                      <tr className="acct-detail-row">
                        <td colSpan={5}>
                          <div className="acct-detail">
                            <div className="acct-detail-head">done today / quota</div>
                            <div className="meters">
                              {meters.map((m) => {
                                const p = m.quota > 0 ? Math.min(100, (m.done / m.quota) * 100) : 0;
                                const complete = m.quota > 0 && m.done >= m.quota;
                                return (
                                  <div key={m.label} className={`meter${complete ? ' done' : ''}`}>
                                    <span className="meter-label">{m.label}</span>
                                    <span className="meter-track"><span className="meter-fill" style={{ width: p + '%' }} /></span>
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
        {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
      </section>

      <ActivityFeed />

      {editing && <AccountForm value={editing} onCancel={() => setEditing(null)} onSave={save} />}
      {logsId && <RunLogs accountId={logsId} onClose={() => setLogsId(null)} />}
      {schedId && <ScheduleView accountId={schedId} onClose={() => setSchedId(null)} />}
    </div>
  );
}

function StatCard({ label, value, sub, bar, tone, tip }) {
  return (
    <div className={`stat-card${tone ? ` tone-${tone}` : ''}`}>
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        {tip && <span className="stat-tip" title={tip} aria-label={tip} role="img">ⓘ</span>}
      </div>
      <div className="stat-val">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
      {bar != null && (
        <div className="stat-bar"><span style={{ width: bar + '%' }} /></div>
      )}
    </div>
  );
}

const BANNER_SLIDES = ['/01.png'];

function Banner() {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (BANNER_SLIDES.length < 2) return;
    const t = setInterval(() => setI((p) => (p + 1) % BANNER_SLIDES.length), 10000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="dash-banner slideshow">
      {BANNER_SLIDES.map((src, idx) => (
        <img key={src} src={src} alt="" className={idx === i ? 'active' : ''} />
      ))}
      {/* <div className="dash-banner-dots">
        {BANNER_SLIDES.map((src, idx) => (
          <span key={src} className={idx === i ? 'active' : ''} />
        ))}
      </div> */}
    </div>
  );
}

function Field({ label, children }) {
  return <label>{label}{children}</label>;
}

function AccountForm({ value, onCancel, onSave }) {
  const [a, setA] = useState(value);
  const [proxies, setProxies] = useState([]);
  const { act, has } = useActions();
  const toast = useToast();
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
            <option value="search_popularity">search by popularity</option>
          </select>
        </Field>
        {a.mode === 'search_popularity' && (
          <div className="pop-fields">
            <div className="grid3">
              <Field label="Min likes (required)">
                <input type="number" min="0" value={a.pop_min_likes || ''} onChange={num('pop_min_likes')} placeholder="e.g. 100" />
              </Field>
              <Field label="Min replies (optional)">
                <input type="number" min="0" value={a.pop_min_replies || ''} onChange={num('pop_min_replies')} placeholder="e.g. 10" />
              </Field>
              <Field label="Search tab">
                <select value={a.pop_tab || 'live'} onChange={(e) => set('pop_tab', e.target.value)}>
                  <option value="live">Latest</option>
                  <option value="top">Top</option>
                </select>
              </Field>
            </div>
            <p className="muted pop-hint">Uses X advanced search — <code>min_faves</code>/<code>min_replies</code>. Latest = recent matching tweets; Top = all-time popular.</p>
          </div>
        )}
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
          <button className="btn btn-primary" disabled={has('save')} onClick={() => act('save', async () => {
            await onSave(a);  // throws on validation/API error -> toast.error
            toast.success(value.id ? 'account updated' : 'account created');
          })}>{has('save') && <Spinner />}{value.id ? 'save' : 'create'}</button>
          <button className="btn" onClick={onCancel} disabled={has('save')}>cancel</button>
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
        <div className="row"><h3>account #{accountId} · runs</h3><button className="btn" onClick={onClose}>close</button></div>
        <table className="dsh-table">
          <thead><tr><th>When</th><th>Trigger</th><th>Status</th><th>Counts</th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>{r.started_at}</td>
                <td>{r.trigger}</td>
                <td className={r.status === 'error' ? 'err' : (r.status === 'done' ? 'ok' : '')}>{r.status}</td>
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
        <div className="row"><h3>account #{accountId} · today's plan</h3><button className="btn" onClick={onClose}>close</button></div>
        {items.length === 0 ? (
          <p className="muted">No actions yet. The scheduler starts the chain on its next tick (if the day window is open and quota remains), or click <b>Replan</b>.</p>
        ) : (
          <table className="dsh-table">
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
const ACT_ING = { post: 'posting', like: 'liking', retweet: 'retweeting', reply: 'replying' };
const STATUS_LABEL = { ok: 'Success', error: 'Failed', skipped: 'Skipped', running: 'In Progress' };

function describeCounts(counts) {
  const parts = [];
  for (const [k, v] of Object.entries(counts || {})) {
    if (!v || k === 'error' || k === 'status') continue;
    const verb = ACT_VERB[k] || k;
    parts.push(v > 1 ? `${verb} ×${v}` : verb);
  }
  return parts.join(', ') || 'ran';
}

function FeedRow({ r, expanded, onToggle, fresh, now }) {
  const remaining = (r.status === 'running' && r.delay_until)
    ? Math.max(0, Math.ceil((new Date(r.delay_until).getTime() - now) / 1000))
    : null;
  // "(new)" beside the exit IP for the first 30 min after a successful action
  const isNew = r.status === 'ok' && r.exit_ip && r.finished_at
    && (now - new Date(r.finished_at).getTime()) < 10 * 60 * 1000;
  return (
    <Fragment>
      <div
        className={`feed-row ${r.status} ${fresh ? 'fresh' : ''} ${expanded ? 'expanded' : ''}`}
        onClick={onToggle} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
        title="click to expand details"
      >
        <span className="feed-time">{fmtJakarta(r.started_at)}</span>
        <span className="feed-acct">
          {r.account_name || '#' + r.account_id}
          {r.exit_ip && <span className="feed-ip" title="exit IP used"> · {r.exit_ip}{isNew && <span className="feed-new"> (new)</span>}</span>}
        </span>
        <span className="feed-act" title={r.status === 'error' ? (r.counts?.error || 'error') : ''}>
          {r.status === 'error' ? (r.counts?.error || 'error')
            : r.status === 'running' ? `${ACT_ING[r.action_type] || 'processing'}…`
            : describeCounts(r.counts)}
        </span>
        <span className="feed-trig">{r.trigger}</span>
        <span className={`feed-status ${r.status}`}>
          <span className="feed-status-dot" />
          {STATUS_LABEL[r.status] || r.status}
          {remaining != null && remaining > 0 ? <span className="feed-countdown"> · {remaining}s</span> : null}
        </span>
        <span className="feed-caret">{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && <FeedDetail r={r} fmt={fmtJakarta} />}
    </Fragment>
  );
}

function ActivityFeed() {
  const [items, setItems] = useState([]);
  const [freshIds, setFreshIds] = useState(() => new Set());
  const [expandedId, setExpandedId] = useState(null);
  const [showErrors, setShowErrors] = useState(false);
  const [now, setNow] = useState(Date.now());
  const prevTop = useRef(0);
  const { act, has } = useActions();
  const toast = useToast();

  const load = () => api.activity().then((rows) => {
    const top = rows.length ? rows[0].id : 0;
    const fresh = prevTop.current > 0
      ? new Set(rows.filter((r) => r.id > prevTop.current).map((r) => r.id))
      : new Set();
    prevTop.current = top;
    setItems(rows);
    setFreshIds(fresh);
  }).catch(() => {});
  useEffect(() => { load(); const i = setInterval(load, 3000); return () => clearInterval(i); }, []);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  const errors = items.filter((r) => r.status === 'error');
  const ok = items.filter((r) => r.status !== 'error');
  const toggle = (id) => setExpandedId(expandedId === id ? null : id);
  const row = (r) => <FeedRow key={r.id} r={r} expanded={expandedId === r.id} onToggle={() => toggle(r.id)} fresh={freshIds.has(r.id)} now={now} />;

  return (
    <section className="px-card dash-card">
      <div className="px-section-head">
        <I.activity /> activity<span className="px-line" />
        <span className="live"><span className="live-dot" /> live</span>
      </div>
      {items.length === 0 ? (
        <p className="muted dash-empty">awaiting activity…</p>
      ) : (
        <>
          {ok.length > 0 ? (
            <div className="feed-list">{ok.map(row)}</div>
          ) : (
            <p className="muted dash-empty">no successful activity yet.</p>
          )}
          {errors.length > 0 && (
            <div className="feed-errors">
              <div className="feed-errors-head" onClick={() => setShowErrors((s) => !s)} role="button" tabIndex={0}
                   onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setShowErrors((s) => !s); } }}>
                <span className="feed-errors-dot" />
                <span className="muted">errors</span>
                <span className="feed-errors-count">{errors.length}</span>
                <span className="px-line" />
                <span className="feed-errors-caret">{showErrors ? '▾' : '▸'}</span>
              </div>
              {showErrors && (
                <>
                  <div className="feed-errors-tools">
                    <span className="muted">bug fixed? remove the failed history:</span>
                    <button className="btn" disabled={has('clearerr')} onClick={() => act('clearerr', async () => {
                      await api.clearErrorActivity();
                      load();
                      toast.success('error activities deleted');
                    })}>{has('clearerr') && <Spinner />} delete error activities</button>
                  </div>
                  <div className="feed-list">{errors.map(row)}</div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function FeedDetail({ r, fmt }) {
  const [log, setLog] = useState(null); // null = loading, string = loaded
  const err = r.counts?.error || '';
  useEffect(() => {
    let alive = true;
    setLog(null);
    api.runLog(r.id)
      .then((res) => { if (alive) setLog(res.log || ''); })
      .catch(() => { if (alive) setLog(''); });
    return () => { alive = false; };
  }, [r.id]);

  const ctx = [
    ['account', r.account_name || '#' + r.account_id],
    ['trigger', r.trigger],
    ['exit IP', r.exit_ip || '—'],
    ['started', fmt(r.started_at) || '—'],
    ['finished', fmt(r.finished_at) || '—'],
    ['status', r.status],
  ];
  return (
    <div className={`feed-detail ${r.status}`}>
      {err && (
        <div className="feed-err">
          <span className="feed-ctx-label">error</span>
          <pre className="feed-err-msg">{err}</pre>
        </div>
      )}
      <div>
        <span className="feed-ctx-label">context</span>
        <div className="feed-ctx-grid">
          {ctx.map(([k, v]) => (
            <div key={k} className="feed-ctx">
              <span className="feed-ctx-k">{k}</span>
              <span className="feed-ctx-v" title={String(v)}>{v}</span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <span className="feed-ctx-label">counts</span>
        <code>{JSON.stringify(r.counts)}</code>
      </div>
      <div>
        <span className="feed-ctx-label">run log · tail</span>
        <pre className="feed-log">{log === null ? 'loading…' : (log || '(no run log file for this run)')}</pre>
      </div>
    </div>
  );
}
