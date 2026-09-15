import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { useActions, useToast, Spinner } from './toast.jsx';
import { fmtJakarta } from './Accounts.jsx';

// Twitter Auto Reply: warm-up accounts get their own search config, engagement
// filters, schedule, limits and system prompt — each runs as an independent
// automation. One page: dashboard + accounts.

const S = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' };
const svg = (children) => (p) => (<svg {...S} {...p}>{children}</svg>);
const I = {
  plus:   svg(<path d="M12 5v14M5 12h14"/>),
  play:   svg(<path d="M6 4l14 8-14 8z"/>),
  bolt:   svg(<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>),
  pause:  svg(<><path d="M9 4v16"/><path d="M15 4v16"/></>),
  stop:   svg(<rect x="5" y="5" width="14" height="14" rx="2"/>),
  pencil: svg(<><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></>),
  trash:  svg(<><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></>),
  reset:  svg(<><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></>),
  list:   svg(<><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="0.6" fill="currentColor" stroke="none"/><circle cx="3.5" cy="12" r="0.6" fill="currentColor" stroke="none"/><circle cx="3.5" cy="18" r="0.6" fill="currentColor" stroke="none"/></>),
  reply:  svg(<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>),
};

const ST_LABEL = { draft: 'draft', active: 'active', paused: 'paused', stopped: 'stopped', error: 'error' };
const ST_TIP = {
  draft: 'configured but not started — open configure (pencil) and activate on save',
  active: 'running — searches every interval and replies up to its limits',
  paused: 'paused by admin — no cycles run until resumed',
  stopped: 'stopped — press play to restart it',
  error: 'last cycle failed — open the activity log (list icon) for the reason, then resume',
};
function StatusBadge({ status }) {
  return <span className={`tag ar-st-${status}`} title={ST_TIP[status] || status}>{ST_LABEL[status] || status}</span>;
}

export default function AutoReply() {
  const [data, setData] = useState(null);
  const [assigning, setAssigning] = useState(false);
  const [editing, setEditing] = useState(null);
  const [logId, setLogId] = useState(null);
  const { act, has } = useActions();
  const toast = useToast();

  const load = () => api.autoreplyOverview().then(setData).catch(() => {});
  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, []);
  const [dryRun, setDryRun] = useState(false);
  useEffect(() => {
    const load = () => api.settings().then((s) => setDryRun(s.dry_run === '1')).catch(() => {});
    load();
    const i = setInterval(load, 30000);
    return () => clearInterval(i);
  }, []);

  const setStatus = (row, status) => act('st-' + row.ar_id, async () => {
    await api.autoreplyUpdate(row.ar_id, { status });
    toast.success(`automation ${status}`);
    load();
  });

  const accounts = data?.accounts || [];
  const stats = data?.stats || {};

  return (
    <div className="dash">
      <header className="dash-head">
        <div className="dash-head-text">
          <h1 className="dash-title">Twitter Auto Reply</h1>
          <p className="dash-sub">Keyword search → engagement filter → AI reply · per-account config</p>
        </div>
        <button className="tab add-tab" onClick={() => setAssigning(true)}>
          <I.plus /> assign account
        </button>
      </header>

      {dryRun && (
        <div className="notice ar-dryrun">
          <span className="notice-glyph">◆</span>
          dry run is ON — replies are generated and recorded but never posted. Turn it off in Settings.
        </div>
      )}

      <div className="stat-grid">
        <StatCard label="Active accounts" tip="Accounts whose auto-reply automation is currently running."
                  value={stats.active_accounts ?? '—'} sub={`of ${accounts.length} assigned`} />
        <StatCard label="Tweets found" tip="Total tweets surfaced by searches across all cycles."
                  value={stats.found ?? '—'} />
        <StatCard label="Qualified" tip="Tweets that passed every filter (keyword, age, engagement, duplicate) and got a reply attempt — replied or failed to post."
                  value={stats.qualified ?? '—'} />
        <StatCard label="Replies posted" tip="Replies successfully posted (generated count includes rejected drafts)."
                  value={stats.posted ?? '—'} sub={`${stats.generated ?? 0} generated`} />
        <StatCard label="Failed" tip="Replies that qualified but failed to post."
                  value={stats.failed ?? '—'} tone={stats.failed ? 'bad' : ''} />
        <StatCard label="Replies today" tip="Replies posted since midnight (Jakarta)."
                  value={stats.today ?? '—'} />
      </div>

      <section className="px-card dash-card">
        <div className="px-section-head">
          <I.reply /> accounts<span className="px-line" />
          <span className="px-count">{accounts.length}</span>
        </div>
        <div className="table-wrap">
          <table className="dsh-table">
            <thead>
              <tr>
                <th>Account</th><th>Warm up</th><th>Automation</th><th>Keywords</th>
                <th>Found</th><th>Replied</th><th>Failed</th><th>Today</th>
                <th>Next run</th><th className="ta-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {!data ? (
                <tr><td colSpan={10} className="muted">loading…</td></tr>
              ) : accounts.length === 0 ? (
                <tr><td colSpan={10} className="dash-empty">no accounts assigned — click “assign account” to pick one from warm up.</td></tr>
              ) : accounts.map((r) => (
                <tr key={r.ar_id}>
                  <td>
                    <span className="acct-name">{r.account_name || `#${r.account_id}`}</span>{' '}
                    <span className="acct-handle">@{r.username}</span>
                  </td>
                  <td title={r.warmup_active ? 'warm-up schedule active' : 'warm-up schedule paused — auto reply runs independently'}>
                    <span className={`ar-dot ${r.warmup_active ? 'on' : ''}`} /> {r.warmup_active ? 'active' : 'paused'}
                  </td>
                  <td><StatusBadge status={r.status} /></td>
                  <td className="ar-kw" title={(r.config.keywords || []).join(' · ')}>
                    {(r.config.keywords || []).join(', ') || <span className="muted">—</span>}
                  </td>
                  <td>{r.found ?? 0}</td>
                  <td className="ok">{r.replied ?? 0}</td>
                  <td className={r.failed ? 'err' : ''}>{r.failed ?? 0}</td>
                  <td title="replies posted today (Jakarta)">
                    {r.today ?? 0}
                  </td>
                  <td title={r.last_run_at ? `last run: ${fmtJakarta(r.last_run_at)}` : 'never run'}>
                    {r.status === 'active'
                      ? (fmtJakarta(r.next_run_at) || <span className="muted">next tick</span>)
                      : <span className="muted">—</span>}
                  </td>
                  <td className="cell-actions">
                    <button className="btn btn-icon" title="run one cycle now — works even when paused; replies up to tweets per cycle" disabled={has('run-' + r.ar_id)}
                            onClick={() => act('run-' + r.ar_id, async () => {
                              const res = await api.autoreplyRun(r.ar_id);
                              toast.success(res.msg || 'cycle queued'); load();
                            })}>{has('run-' + r.ar_id) ? <Spinner /> : <I.bolt />}</button>
                    {r.status === 'active' ? (
                      <button className="btn btn-icon" title="pause automation" disabled={has('st-' + r.ar_id)}
                              onClick={() => setStatus(r, 'paused')}><I.pause /></button>
                    ) : (
                      <button className="btn btn-icon" title={r.status === 'paused' || r.status === 'error' ? 'resume automation' : 'start automation'}
                              disabled={has('st-' + r.ar_id)}
                              onClick={() => setStatus(r, 'active')}><I.play /></button>
                    )}
                    {r.status !== 'stopped' && r.status !== 'draft' && (
                      <button className="btn btn-icon" title="stop automation" disabled={has('st-' + r.ar_id)}
                              onClick={() => setStatus(r, 'stopped')}><I.stop /></button>
                    )}
                    <button className="btn btn-icon" title="configure" onClick={() => setEditing(r)}><I.pencil /></button>
                    <button className="btn btn-icon" title="activity log" onClick={() => setLogId(r)}><I.list /></button>
                    <button className="btn btn-icon" title="reset — clear tweet history so every tweet is eligible again" disabled={has('rst-' + r.ar_id)}
                            onClick={() => act('rst-' + r.ar_id, async () => {
                              if (!confirm(`Reset ${r.account_name || '@' + r.username}? Tweet history is cleared — all tweets become eligible for replies again.`)) return;
                              const res = await api.autoreplyReset(r.ar_id);
                              toast.success(`reset — ${res.cleared} tweets eligible again`); load();
                            })}>{has('rst-' + r.ar_id) ? <Spinner /> : <I.reset />}</button>
                    <button className="btn btn-icon btn-danger" title="unassign" disabled={has('del-' + r.ar_id)}
                            onClick={() => act('del-' + r.ar_id, async () => {
                              if (!confirm(`Unassign ${r.account_name || '@' + r.username} from auto reply? History will be removed.`)) return;
                              await api.autoreplyRemove(r.ar_id);
                              toast.success('account unassigned'); load();
                            })}>{has('del-' + r.ar_id) ? <Spinner /> : <I.trash />}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted ar-hint">
          Each account runs independently: its own keywords, engagement thresholds, schedule and system prompt.
          Failures only pause the affected account. Search &amp; reply cycles appear in the global activity feed too.
        </p>
      </section>

      <LiveActivity />

      {assigning && (
        <AssignModal
          available={data?.available || []}
          onClose={() => setAssigning(false)}
          onAssigned={async (arId) => {
            setAssigning(false);
            const ov = await api.autoreplyOverview().catch(() => null);
            if (ov) setData(ov);
            setEditing((ov?.accounts || []).find((a) => a.ar_id === arId) || null);
          }}
        />
      )}
      {editing && <ConfigModal row={editing} onClose={() => setEditing(null)} onSaved={load} />}
      {logId && <LogModal row={logId} onClose={() => setLogId(null)} />}
    </div>
  );
}

function StatCard({ label, value, sub, tone, tip }) {
  return (
    <div className={`stat-card${tone ? ` tone-${tone}` : ''}`}>
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        {tip && <span className="stat-tip" title={tip} aria-label={tip} role="img">ⓘ</span>}
      </div>
      <div className="stat-val">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

// ---- assign a warm-up account ------------------------------------------

function AssignModal({ available, onClose, onAssigned }) {
  const [accountId, setAccountId] = useState('');
  const { act, has } = useActions();
  const toast = useToast();
  useEffect(() => { if (available.length) setAccountId(String(available[0].id)); }, [available]);
  return (
    <div className="modal">
      <div className="modal-card">
        <h3>Assign warm-up account</h3>
        {available.length === 0 ? (
          <p className="muted">Every warm-up account is already assigned. Add accounts on the Warm Up page first.</p>
        ) : (
          <>
            <label>Account
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {available.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} @{a.username}{a.has_cookies ? '' : ' (no cookies)'}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted pop-hint">Starts as <b>draft</b> — configure keywords &amp; system prompt, then set it active.</p>
          </>
        )}
        <div className="row">
          {available.length > 0 && (
            <button className="btn btn-primary" disabled={has('assign') || !accountId}
                    onClick={() => act('assign', async () => {
                      const res = await api.autoreplyAssign({ account_id: +accountId });
                      toast.success('account assigned — configure it now');
                      await onAssigned(res.ar_id);
                    })}>{has('assign') && <Spinner />}assign</button>
          )}
          <button className="btn" onClick={onClose} disabled={has('assign')}>close</button>
        </div>
      </div>
    </div>
  );
}

// ---- per-account configuration -----------------------------------------

const csv = (v) => (Array.isArray(v) ? v.join(', ') : (v || ''));
const fromCsv = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);

function ConfigModal({ row, onClose, onSaved }) {
  const [c, setC] = useState(() => ({
    ...row.config,
    keywords: csv(row.config.keywords),
    exclude_keywords: csv(row.config.exclude_keywords),
    banned_words: csv(row.config.banned_words),
  }));
  const initial = useRef(JSON.stringify(c));
  const { act, has } = useActions();
  const toast = useToast();
  const set = (k, v) => setC((p) => ({ ...p, [k]: v }));
  const num = (k) => (e) => set(k, e.target.value);
  const [startNow, setStartNow] = useState(row.status === 'active');

  // must be a closure: act() is async — calling it in render body would run the
  // save immediately and close the modal on mount
  const save = () => act('cfg', async () => {
    await api.autoreplyUpdate(row.ar_id, {
      config: {
        ...c,
        keywords: fromCsv(c.keywords),
        exclude_keywords: fromCsv(c.exclude_keywords),
        banned_words: fromCsv(c.banned_words),
        min_likes: +c.min_likes || 0, min_retweets: +c.min_retweets || 0,
        min_replies: +c.min_replies || 0, max_age_hours: +c.max_age_hours || 0,
        tweets_per_cycle: +c.tweets_per_cycle || 0, interval_minutes: +c.interval_minutes || 1,
        cooldown_seconds: +c.cooldown_seconds || 0, max_reply_chars: +c.max_reply_chars || 280,
      },
    });
    if (startNow && row.status !== 'active') await api.autoreplyUpdate(row.ar_id, { status: 'active' });
    toast.success('configuration saved');
    onSaved();
    onClose();
  });

  return (
    <div className="modal">
      <div className="modal-card wide">
        <h3>@{row.username} · auto reply config</h3>

        <h4 className="ar-sec">search</h4>
        <label>Keywords (comma-separated, OR-combined)
          <input value={c.keywords} onChange={(e) => set('keywords', e.target.value)} placeholder="AI automation, SaaS tools" />
        </label>
        <label>Exclude keywords (comma-separated)
          <input value={c.exclude_keywords} onChange={(e) => set('exclude_keywords', e.target.value)} placeholder="job, giveaway" />
        </label>
        <div className="grid3">
          <label>Language (blank = any)<input value={c.lang || ''} onChange={(e) => set('lang', e.target.value)} placeholder="en / id" /></label>
          <label>Sort
            <select value={c.sort || 'live'} onChange={(e) => set('sort', e.target.value)}>
              <option value="live">Latest</option>
              <option value="top">Top</option>
            </select>
          </label>
          <label>Max tweet age (hours)<input type="number" min="0" value={c.max_age_hours} onChange={num('max_age_hours')} /></label>
        </div>
        <div className="grid3">
          <label>Min likes<input type="number" min="0" value={c.min_likes} onChange={num('min_likes')} /></label>
          <label>Min retweets<input type="number" min="0" value={c.min_retweets} onChange={num('min_retweets')} /></label>
          <label>Min replies<input type="number" min="0" value={c.min_replies} onChange={num('min_replies')} /></label>
        </div>
        <p className="muted pop-hint">Uses X advanced search — <code>min_faves</code>, <code>min_retweets</code>, <code>min_replies</code>, <code>lang</code>, <code>since</code> — plus a client-side re-check, so only tweets meeting all thresholds are processed.</p>

        <h4 className="ar-sec">schedule</h4>
        <div className="grid4">
          <label>Search interval (min)<input type="number" min="1" value={c.interval_minutes} onChange={num('interval_minutes')} /></label>
          <label>Tweets per cycle<input type="number" min="1" value={c.tweets_per_cycle} onChange={num('tweets_per_cycle')} /></label>
          <label>Cooldown between replies (s)<input type="number" min="0" value={c.cooldown_seconds} onChange={num('cooldown_seconds')} /></label>
          <label>Max reply length (chars)<input type="number" min="1" value={c.max_reply_chars} onChange={num('max_reply_chars')} /></label>
        </div>
        <p className="muted pop-hint">Tweets per cycle is the only limit — one cycle replies to at most that many tweets, then waits for the next interval.</p>

        <h4 className="ar-sec">AI system prompt</h4>
        <label>System prompt (personality of this account)
          <textarea rows="10" value={c.system_prompt || ''} onChange={(e) => set('system_prompt', e.target.value)} />
        </label>
        <label>Banned words in replies (comma-separated)
          <input value={c.banned_words} onChange={(e) => set('banned_words', e.target.value)} placeholder="discord, DM, click link" />
        </label>
        <p className="muted pop-hint">Replies are validated before posting: max length, banned words, and no duplicate responses within a cycle.</p>

        <label className="check">
          <input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} />
          {row.status === 'active' ? 'automation stays active' : 'activate automation on save'}
        </label>

        <div className="row">
          <button className="btn btn-primary" disabled={has('cfg') || !fromCsv(c.keywords).length} onClick={save}>
            {has('cfg') && <Spinner />}save
          </button>
          <button className="btn" onClick={() => {
            if (JSON.stringify(c) === initial.current || confirm('Discard changes?')) onClose();
          }} disabled={has('cfg')}>cancel</button>
        </div>
      </div>
    </div>
  );
}

// ---- live activity (all accounts) ----------------------------------------

function LiveActivity() {
  const [data, setData] = useState(null);   // null = first load -> skeleton
  useEffect(() => {
    const load = () => api.autoreplyActivity().then(setData).catch(() => {});
    load();
    const i = setInterval(load, 2000);   // steps appear as they happen
    return () => clearInterval(i);
  }, []);
  const items = (data && data.items) || [];
  return (
    <section className="px-card dash-card">
      <div className="px-section-head">
        <I.reply /> live activity<span className="px-line" />
        {data !== null && <span className="px-count">{items.length}</span>}
      </div>
      {data && data.current && (
        <div className="notice ar-running">
          <span className="notice-glyph">▶</span>
          cycle running on <b>@{data.current.name}</b> ({data.current.trigger}) — searching, filtering and replying…
        </div>
      )}
      {data === null ? (
        <div>
          <div className="ar-skel" style={{ width: '72%' }} />
          <div className="ar-skel" style={{ width: '92%' }} />
          <div className="ar-skel" style={{ width: '55%' }} />
        </div>
      ) : items.length === 0 ? (
        <p className="muted ar-hint">no activity yet — cycles and replies appear here live.</p>
      ) : (
        <div className="mass-log ar-log">
          {items.map((l) => (
            <div key={l.id} className={`mass-log-row${l.level === 'error' ? ' err' : ''}`}>
              <span className="mass-log-ts">{fmtJakarta(l.ts)}</span>
              <span className="ar-log-acct">@{l.account_username || l.account_id}</span>
              <span className={`mass-log-glyph${l.level === 'error' ? ' err' : ''}`}>{l.level === 'error' ? '✕' : '·'}</span>
              <span className="mass-log-msg">{l.message}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ---- per-account activity log -------------------------------------------

function LogModal({ row, onClose }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const load = () => api.autoreplyLog(row.ar_id).then(setItems).catch(() => {});
    load();
    const i = setInterval(load, 4000);
    return () => clearInterval(i);
  }, [row.ar_id]);
  return (
    <div className="modal">
      <div className="modal-card wide">
        <div className="row">
          <h3>@{row.username} · activity log</h3>
          <button className="btn" onClick={onClose}>close</button>
        </div>
        {items.length === 0 ? (
          <p className="muted">no activity yet.</p>
        ) : (
          <div className="mass-log ar-log">
            {[...items].reverse().map((l) => (
              <div key={l.id} className={`mass-log-row${l.level === 'error' ? ' err' : ''}`}>
                <span className="mass-log-ts">{fmtJakarta(l.ts)}</span>
                <span className={`mass-log-glyph${l.level === 'error' ? ' err' : ''}`}>{l.level === 'error' ? '✕' : '·'}</span>
                <span className="mass-log-msg">{l.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
