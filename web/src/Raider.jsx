import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { useActions, useToast, Spinner, errorMsg } from './toast.jsx';
import MassRaid from './MassRaid.jsx';
import RaidActivity from './RaidActivity.jsx';

// accept a full X post URL or a bare numeric id -> numeric id (or '')
function parseTweetId(raw) {
  const s = (raw || '').trim();
  if (!s) return '';
  const m = s.match(/\/status\/(\d+)/);
  if (m) return m[1];
  return /^\d+$/.test(s) ? s : '';
}

const ACTIONS = ['like', 'retweet', 'reply'];
const STATUS_LABEL = { idle: '—', queued: 'Queued', running: 'Running', done: 'Success', error: 'Failed' };
const ACT_PAST = { like: 'liked', retweet: 'retweeted', reply: 'replied', post: 'posted' };

const STATUS_FILTERS = [['any', 'any'], ['active', 'active'], ['inactive', 'inactive']];

function relTime(iso, now = Date.now()) {
  if (!iso) return '';
  const d = new Date(iso).getTime();
  if (isNaN(d)) return '';
  const s = Math.max(0, (now - d) / 1000);
  if (s < 60) return 'Just Now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} Min Ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} Hr Ago`;
  const dd = Math.floor(h / 24);
  if (dd < 7) return `${dd} Day${dd > 1 ? 's' : ''} Ago`;
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function lastLabel(a, now) {
  const la = a.last_activity;
  if (!la || !la.action) return '—';
  return `Last ${ACT_PAST[la.action] || la.action} ${relTime(la.ts, now)}`;
}

export default function Raider() {
  const [accounts, setAccounts] = useState([]);
  const [input, setInput] = useState('');
  const [selected, setSelected] = useState(() => new Set());
  const [action, setAction] = useState('like');
  const [replyText, setReplyText] = useState('');
  const [results, setResults] = useState({});
  const [tab, setTab] = useState('single');
  // scalable selection: search + status filter
  const [q, setQ] = useState('');
  const [statusF, setStatusF] = useState('any');
  const [now, setNow] = useState(Date.now());
  const [stepGap, setStepGap] = useState(0); // settings: raid_step_gap seconds
  const allRef = useRef(null);
  const { act, has } = useActions();
  const toast = useToast();

  useEffect(() => {
    Promise.all([api.accounts(), api.accountActivity(), api.settings()])
      .then(([accs, act, st]) => {
        setAccounts(accs.map((a) => ({ ...a, last_activity: act[a.id] || null })));
        setStepGap(Math.max(0, +st.raid_step_gap || 0));
      })
      .catch((e) => toast.error(errorMsg(e)));
  }, [toast]);
  // keep "X Min Ago" fresh while the page sits open
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 60000); return () => clearInterval(t); }, []);

  const tweetId = parseTweetId(input);
  const running = has('raid');

  // ---- filtering (search + status) ----
  const qL = q.trim().toLowerCase();
  const filtered = accounts.filter((a) => {
    if (statusF === 'active' && !a.active) return false;
    if (statusF === 'inactive' && a.active) return false;
    if (qL && !(`${a.name} ${a.username}`.toLowerCase().includes(qL))) return false;
    return true;
  });
  const visibleIds = filtered.map((a) => a.id);
  const allVisible = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const someVisible = visibleIds.some((id) => selected.has(id));
  useEffect(() => { if (allRef.current) allRef.current.indeterminate = someVisible && !allVisible; }, [someVisible, allVisible]);

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  const toggleAllVisible = () => setSelected((s) => {
    const n = new Set(s);
    if (visibleIds.every((id) => n.has(id))) visibleIds.forEach((id) => n.delete(id));
    else visibleIds.forEach((id) => n.add(id));
    return n;
  });
  const selectAll = () => setSelected(new Set(accounts.map((a) => a.id)));
  const clearAll = () => setSelected(new Set());

  // selected accounts (independent of the current filter) -> what actually runs
  const ids = accounts.filter((a) => selected.has(a.id));

  const run = () => act('raid', async () => {
    if (!tweetId) { toast.error('Enter a valid tweet URL or ID.'); return; }
    if (ids.length === 0) { toast.error('Select at least one account.'); return; }
    if (action === 'reply' && !replyText.trim()) { toast.error('Reply text is required.'); return; }
    setResults(Object.fromEntries(ids.map((a) => [a.id, { status: 'queued' }])));
    for (let i = 0; i < ids.length; i++) {
      const a = ids[i];
      setResults((p) => ({ ...p, [a.id]: { status: 'running' } }));
      try {
        const r = await api.raidRun({ account_id: a.id, tweet_id: tweetId, action, reply_text: replyText });
        const ok = r.status === 'ok';
        setResults((p) => ({ ...p, [a.id]: { status: ok ? 'done' : 'error', message: r.message || (ok ? 'done' : 'failed'), exit_ip: r.exit_ip || '' } }));
        if (ok) toast.success(`${a.name}: ${action} succeeded${r.exit_ip ? ' · ' + r.exit_ip : ''}`);
        else toast.error(`${a.name}: ${r.message || 'failed'}`);
      } catch (e) {
        const m = errorMsg(e);
        setResults((p) => ({ ...p, [a.id]: { status: 'error', message: m } }));
        toast.error(`${a.name}: ${m}`);
      }
      // settings pacing: randomized step gap (±50%) between accounts
      if (stepGap > 0 && i < ids.length - 1) {
        await new Promise((r) => setTimeout(r, stepGap * 1000 * (0.5 + Math.random())));
      }
    }
    toast.success('raid complete');
  });

  const canRun = tweetId && ids.length > 0 && (action !== 'reply' || replyText.trim());
  const hasResults = Object.keys(results).length > 0;
  // raid progress aggregates for the thin bar + header count
  const raidDone = ids.filter((a) => ['done', 'error'].includes((results[a.id] || {}).status)).length;
  const raidOk = ids.filter((a) => (results[a.id] || {}).status === 'done').length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="prompt">
          <span className="p-user">raider</span>
          <span className="p-at">@</span>
          <span className="p-host">mhfcorp</span>
          <span className="p-sep">:</span>
          <span className="p-path">~/raider</span>
          <span className="p-end">#</span>
        </div>
        <div className="status"><span className="badge badge-idle">raid a post</span></div>
      </header>

      <main className="raid-shell">
        <div className="raid-main">
        <div className="raid-tabs">
          <button className={`raid-tab${tab === 'single' ? ' active' : ''}`} onClick={() => setTab('single')}>Single Raid</button>
          <button className={`raid-tab${tab === 'mass' ? ' active' : ''}`} onClick={() => setTab('mass')}>Mass Raid</button>
        </div>
        {tab === 'single' ? (
        <>
        {/* target */}
        <section className="px-card">
          <div className="px-section-head">target lock</div>
          <div className="raid-target-wrap">
            <span className="raid-target-prompt">»</span>
            <input
              className="raid-target"
              placeholder="paste the X post URL or tweet ID — https://x.com/user/status/2075067079388958992"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
          </div>
          {input.trim() && (
            <div className="raid-tweetid">
              {tweetId
                ? <span className="ok"><span className="led on" /> locked · <code>{tweetId}</code></span>
                : <span className="err"><span className="led err" /> invalid — paste a full X post URL or the numeric tweet ID</span>}
            </div>
          )}
        </section>

        {/* accounts — structured table like the dashboard, with last-activity */}
        <section className="px-card">
          <div className="px-section-head">
            accounts<span className="px-line" />
            <span className="px-count">{selected.size} selected · {accounts.length} total</span>
          </div>

          <div className="raid-filters">
            <input className="raid-search" placeholder="search name or @handle…" value={q}
                   onChange={(e) => setQ(e.target.value)} disabled={running} />
            <div className="raid-chips">
              <span className="raid-chip-label">status</span>
              {STATUS_FILTERS.map(([v, l]) => (
                <button key={v} className={`chip${statusF === v ? ' on' : ''}`} disabled={running} onClick={() => setStatusF(v)}>{l}</button>
              ))}
            </div>
            <div className="raid-tools">
              <span className="muted raid-shown">{filtered.length} shown</span>
              <button className="btn link" disabled={running} onClick={selectAll}>all</button>
              <button className="btn link" disabled={running} onClick={clearAll}>clear</button>
            </div>
          </div>

          <div className="table-wrap raid-acct-scroll">
            <table className="dsh-table raid-tbl">
              <thead>
                <tr>
                  <th className="raid-th-check">
                    <input type="checkbox" ref={allRef} checked={allVisible} onChange={toggleAllVisible}
                           disabled={running || visibleIds.length === 0} title="select all visible" />
                  </th>
                  <th>Account</th>
                  <th>Last activity</th>
                  <th className="ta-right">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={4} className="muted raid-empty">no accounts match the current filters.</td></tr>
                ) : filtered.map((a) => (
                  <tr key={a.id} className={`raid-tr${selected.has(a.id) ? ' sel' : ''}${!a.active ? ' dim' : ''}`}
                      onClick={() => !running && toggle(a.id)}>
                    <td className="raid-td-check" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggle(a.id)} disabled={running} />
                    </td>
                    <td>
                      <div className="acct-name-cell">
                        <span className="acct-name">{a.name}</span>
                        <span className="acct-handle">@{a.username}</span>
                      </div>
                    </td>
                    <td className="raid-last">{lastLabel(a, now)}</td>
                    <td className="ta-right">
                      <span className="tag">{a.active ? 'active' : 'off'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* action */}
        <section className="px-card">
          <div className="px-section-head">payload<span className="px-line" /><span className="px-count">{ids.length} armed</span></div>
          <div className="seg raid-seg">
            {ACTIONS.map((a) => (
              <button key={a} className={action === a ? 'active' : ''} disabled={running} onClick={() => setAction(a)}>{a}</button>
            ))}
          </div>
          {action === 'reply' && (
            <label className="set-field" style={{ marginTop: 12 }}>
              <span className="set-field-label">Reply text</span>
              <textarea rows={3} value={replyText} disabled={running}
                placeholder="the reply each selected account will post"
                onChange={(e) => setReplyText(e.target.value)} />
            </label>
          )}
        </section>

        {/* run + progress */}
        <div className="raid-bar">
          <span className="raid-summary">
            {ids.length} armed × {action}{tweetId ? <span className="raid-summary-id"> → {tweetId}</span> : ''}
          </span>
          <button className="btn btn-primary raid-exec" disabled={!canRun || running} onClick={run}>
            {running && <Spinner />} {running ? 'raiding…' : 'execute ⏎'}
          </button>
        </div>

        {(running || hasResults) && (
          <section className="px-card">
            <div className="px-section-head">
              progress<span className="px-line" />
              <span className="px-count">{raidDone}/{ids.length} done · {raidOk} ok</span>
            </div>
            <div className="raid-agg"><span style={{ width: ids.length ? `${(raidDone / ids.length) * 100}%` : 0 }} /></div>
            <div className="raid-progress">
              {ids.map((a) => {
                const r = results[a.id] || { status: 'idle' };
                return (
                  <div key={a.id} className={`raid-row ${r.status}`}>
                    <span className="raid-row-name">{a.name} <span className="raid-acct-handle">@{a.username}</span></span>
                    {r.exit_ip && <span className="raid-row-ip" title="exit IP">{r.exit_ip}</span>}
                    {r.message && r.status === 'error' && <span className="raid-row-msg err" title={r.message}>{r.message}</span>}
                    <span className={`raid-status ${r.status}`}>
                      <span className="raid-status-dot" />{STATUS_LABEL[r.status] || r.status}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        )}
        </>
        ) : (
          <MassRaid />
        )}
        </div>

        <aside className="raid-aside">
          <RaidActivity />
        </aside>
      </main>
    </div>
  );
}
