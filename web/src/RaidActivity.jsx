import { useEffect, useState } from 'react';
import { api } from './api.js';

const STATUS = {
  running: { label: 'Running', cls: 'running' },
  completed: { label: 'Completed', cls: 'ok' },
  failed: { label: 'Failed', cls: 'err' },
  partial: { label: 'Partial', cls: 'err' },
};
const ACT_LABEL = { like: 'Likes', retweet: 'Retweets', reply: 'Replies' };
const ACT_SING = { like: 'Like', retweet: 'Retweet', reply: 'Reply' };

function relTime(iso, now) {
  if (!iso) return '—';
  const d = new Date(iso).getTime();
  if (isNaN(d)) return '—';
  const s = Math.max(0, Math.floor((now - d) / 1000));
  if (s < 60) return `${s} seconds ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`;
  return `${Math.floor(h / 24)} day${h < 48 ? '' : 's'} ago`;
}
const fmtClock = (iso) => iso ? new Date(iso).toLocaleTimeString('en-US', { hour12: false }) : '—';

function RaidCard({ r, now }) {
  const [open, setOpen] = useState(false);
  const st = STATUS[r.status] || { label: r.status, cls: '' };
  const req = r.requested || {};
  const sum = r.summary || {};
  const reqActions = ['like', 'retweet', 'reply'].filter((k) => req[k] > 0);
  const parts = reqActions.map((k) => `${req[k]} ${ACT_SING[k]}`);
  const accounts = [...new Set((r.accounts || []).map((a) => a.username))];

  return (
    <div className={`raidcard ${st.cls}`}>
      <div className="raidcard-head" onClick={() => setOpen((o) => !o)} role="button" tabIndex={0}
           onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => !o); } }}>
        <span className={`raidcard-dot ${st.cls}`} />
        <span className={`raidcard-status ${st.cls}`}>{st.label}</span>
        <span className="raidcard-time">{r.status === 'running' ? `started ${relTime(r.started_at, now)}` : relTime(r.finished_at || r.started_at, now)}</span>
        <span className="raidcard-caret">{open ? '▾' : '▸'}</span>
      </div>

      <div className="raidcard-meta">
        {r.kind === 'mass' ? 'Mass Raid' : 'Single Raid'}{parts.length ? ` • ${parts.join(' • ')}` : ''}{accounts.length ? ` • ${accounts.length} account${accounts.length === 1 ? '' : 's'}` : ''}
      </div>

      {r.status === 'running' && reqActions.length > 0 && (
        <div className="raidcard-progress">
          {reqActions.map((k) => (
            <span key={k} className="raidcard-prog-item">{ACT_LABEL[k]} <b>{(sum[k] || {}).ok || 0}</b>/{req[k]}</span>
          ))}
        </div>
      )}

      {accounts.length > 0 && (
        <div className="raidcard-accts">
          {accounts.slice(0, 24).map((u) => <span key={u} className="chip-acct">@{u}</span>)}
          {accounts.length > 24 && <span className="chip-acct more">+{accounts.length - 24}</span>}
        </div>
      )}

      {open && (
        <div className="raidcard-body">
          <div className="raidcard-grid">
            <div><span className="raidcard-k">Tweet ID</span><div className="raidcard-v">{r.tweet_id}</div></div>
            <div><span className="raidcard-k">Status</span><div className={`raidcard-v ${st.cls}`}>{st.label}</div></div>
          </div>
          <div className="raidcard-sub">Summary</div>
          <div className="raidcard-summary">
            {['like', 'retweet', 'reply'].map((k) => (
              <div key={k} className={(sum[k] || {}).fail ? 'err' : 'ok'}>
                <span className="raidcard-mark">{(sum[k] || {}).fail ? '✕' : '✓'}</span> {ACT_LABEL[k]}: {(sum[k] || {}).ok || 0}/{req[k] || 0}
              </div>
            ))}
          </div>
          {(r.failed || []).length > 0 && (
            <>
              <div className="raidcard-sub err">Failed Accounts</div>
              <div className="raidcard-failed">
                {r.failed.map((f, i) => (
                  <div key={i} className="raidcard-failed-row"><span>@{f.username}</span><span className="muted">{f.message || 'failed'}</span></div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function RaidActivity() {
  const [items, setItems] = useState(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const load = () => api.raids().then(setItems).catch(() => {});
    load();
    const i = setInterval(load, 3000);
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => { clearInterval(i); clearInterval(t); };
  }, []);

  return (
    <section className="px-card raid-activity">
      <div className="px-section-head">
        live activity<span className="px-line" />
        {items && <span className="px-count">{items.length} raid{items.length === 1 ? '' : 's'}</span>}
      </div>
      {items && items.length === 0 ? (
        <p className="muted raid-empty">no raids yet — run a single or mass raid to see history here.</p>
      ) : items && (
        <div className="raidcards">{items.map((r) => <RaidCard key={r.id} r={r} now={now} />)}</div>
      )}
    </section>
  );
}
