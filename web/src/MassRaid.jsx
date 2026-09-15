import { useState } from 'react';
import { api } from './api.js';
import { useActions, useToast, Spinner, errorMsg } from './toast.jsx';

function parseTweetId(raw) {
  const s = (raw || '').trim();
  if (!s) return '';
  const m = s.match(/\/status\/(\d+)/);
  if (m) return m[1];
  return /^\d+$/.test(s) ? s : '';
}
const ACT_LABEL = { like: 'Likes', retweet: 'Retweets', reply: 'Replies' };
const fmtTime = (d) => new Date(d).toLocaleTimeString('en-US', { hour12: false });
const fmtDur = (s) => s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;

export default function MassRaid() {
  const [input, setInput] = useState('');
  const [likes, setLikes] = useState(5);
  const [retweets, setRetweets] = useState(5);
  const [replies, setReplies] = useState(0);
  const [replyText, setReplyText] = useState('');
  const [plan, setPlan] = useState(null);
  const [log, setLog] = useState([]);
  const [stats, setStats] = useState(null);
  const { act, has } = useActions();
  const toast = useToast();

  const tweetId = parseTweetId(input);
  const planning = has('plan');
  const running = has('massrun');
  const errors = plan?.errors || [];
  const counts = { like: +likes || 0, retweet: +retweets || 0, reply: +replies || 0 };
  const canPlan = tweetId && (counts.like || counts.retweet || counts.reply) && (counts.reply === 0 || replyText.trim());
  const canRun = !!plan && errors.length === 0 && plan.plan.length > 0;

  const makePlan = () => act('plan', async () => {
    const r = await api.massPlan({ tweet_id: tweetId, likes: counts.like, retweets: counts.retweet, replies: counts.reply });
    setPlan(r);
    setLog([]); setStats(null);
    if (r.errors.length) {
      toast.error('Not enough eligible accounts — see the preview.');
    } else {
      const accs = new Set(r.plan.map((p) => p.account_id)).size;
      toast.success(`${r.plan.length} actions queued across ${accs} account${accs === 1 ? '' : 's'}`);
    }
  });

  const run = () => act('massrun', async () => {
    const steps = plan.plan;
    setLog([]); setStats({ like: { ok: 0, fail: 0 }, retweet: { ok: 0, fail: 0 }, reply: { ok: 0, fail: 0 } });
    const t0 = Date.now();
    const start = await api.massStart({ tweet_id: tweetId, likes: counts.like, retweets: counts.retweet, replies: counts.reply });
    const raidId = start.raid_id;
    for (const step of steps) {
      const entry = { key: `${step.account_id}-${step.action}-${Math.random().toString(36).slice(2, 7)}`,
                      name: step.name, username: step.username, action: step.action, result: 'running', message: '', ts: Date.now() };
      setLog((l) => [...l, entry]);   // show the step as "waiting…" while the pre-action delay runs
      try {
        const r = await api.raidRun({ account_id: step.account_id, tweet_id: tweetId, action: step.action, reply_text: replyText, raid_id: raidId });
        const ok = r.status === 'ok';
        setStats((p) => { const n = { ...p }; ok ? n[step.action].ok++ : n[step.action].fail++; return n; });
        setLog((l) => l.map((e) => e.key === entry.key ? { ...e, result: ok ? 'ok' : 'error', message: ok ? '' : (r.message || 'failed'), ts: Date.now() } : e));
        if (!ok) toast.error(`${step.name}: ${step.action} failed — ${r.message || ''}`);
      } catch (e) {
        const m = errorMsg(e);
        setStats((p) => { const n = { ...p }; n[step.action].fail++; return n; });
        setLog((l) => l.map((e) => e.key === entry.key ? { ...e, result: 'error', message: m, ts: Date.now() } : e));
        toast.error(`${step.name}: ${m}`);
      }
    }
    await api.massFinish({ raid_id: raidId, status: 'completed' });
    const dur = Math.max(1, Math.round((Date.now() - t0) / 1000));
    setStats((p) => ({ ...p, duration: dur }));
    toast.success('mass raid complete');
  });

  const totalFail = stats ? (stats.like.fail + stats.retweet.fail + stats.reply.fail) : 0;
  const totalDone = stats ? (stats.like.ok + stats.retweet.ok + stats.reply.ok + totalFail) : 0;
  const rate = totalDone ? Math.round(((totalDone - totalFail) / totalDone) * 100) : 0;

  return (
    <>
      {/* target */}
      <section className="px-card">
        <div className="px-section-head">target post</div>
        <input className="raid-target" placeholder="paste the X post URL or tweet ID…"
               value={input} onChange={(e) => setInput(e.target.value)} disabled={running} />
        {input.trim() && (
          <div className="raid-tweetid">
            {tweetId ? <span className="ok">tweet id: <code>{tweetId}</code></span>
                     : <span className="err">invalid — paste a full X post URL or the numeric tweet ID</span>}
          </div>
        )}
      </section>

      {/* actions */}
      <section className="px-card">
        <div className="px-section-head">actions <span className="px-line" /> <span className="px-count">distributed automatically</span></div>
        <div className="grid3">
          {[['like', likes, setLikes, 'Likes'], ['retweet', retweets, setRetweets, 'Retweets'], ['reply', replies, setReplies, 'Replies']].map(([k, v, set, lbl]) => (
            <label className="set-field" key={k}>
              <span className="set-field-label">{lbl}</span>
              <input type="number" min="0" value={v} disabled={running || planning}
                     onChange={(e) => set(+e.target.value || 0)} />
            </label>
          ))}
        </div>
        {counts.reply > 0 && (
          <label className="set-field" style={{ marginTop: 12 }}>
            <span className="set-field-label">Reply template</span>
            <textarea rows={3} value={replyText} disabled={running || planning}
              placeholder="the reply each selected account will post"
              onChange={(e) => setReplyText(e.target.value)} />
          </label>
        )}
        <div className="raid-bar" style={{ marginTop: 14 }}>
          <span className="raid-count">accounts are selected automatically — no manual picking</span>
          <button className="btn btn-primary" disabled={!canPlan || planning || running} onClick={makePlan}>
            {planning && <Spinner />} {planning ? 'planning…' : 'plan raid'}
          </button>
        </div>
      </section>

      {/* preview / validation */}
      {plan && (
        <section className="px-card">
          <div className="px-section-head">preview</div>
          {errors.length > 0 ? (
            <div className="mass-errors">
              <p className="err" style={{ margin: '0 0 8px' }}>Not enough eligible accounts:</p>
              {errors.map((e) => (
                <p key={e.action} className="muted" style={{ margin: '2px 0' }}>
                  {ACT_LABEL[e.action]}: requested <b>{e.requested}</b>, only <b className="err">{e.eligible}</b> eligible.
                </p>
              ))}
              <p className="muted" style={{ margin: '8px 0 0' }}>Reduce the requested amount or activate more accounts.</p>
            </div>
          ) : (
            <div className="mass-preview">
              {['like', 'retweet', 'reply'].map((k) => counts[k] > 0 && (
                <div className="mass-preview-row" key={k}>
                  <span className="mass-preview-act">{ACT_LABEL[k]}</span>
                  <span className="ok">{plan.requested[k]} of {plan.eligible[k]} eligible</span>
                </div>
              ))}
              <div className="mass-preview-row muted">
                <span>accounts selected automatically</span>
                <span>{new Set(plan.plan.map((p) => p.account_id)).size} accounts · {plan.plan.length} actions</span>
              </div>
            </div>
          )}
        </section>
      )}

      {/* run */}
      <div className="raid-bar">
        <span className="raid-count">{plan && errors.length === 0 ? `${plan.plan.length} actions ready` : 'plan first to enable run'}</span>
        <button className="btn btn-primary" disabled={!canRun || running} onClick={run}>
          {running && <Spinner />} {running ? 'raiding…' : 'run mass raid'}
        </button>
      </div>

      {/* live log */}
      {(running || log.length > 0) && (
        <section className="px-card">
          <div className="px-section-head">activity log <span className="px-line" /> <span className="px-count">{log.length} events</span></div>
          <div className="mass-log">
            {log.map((e) => (
              <div key={e.key} className={`mass-log-row ${e.result}`}>
                <span className="mass-log-ts">{fmtTime(e.ts)}</span>
                <span className="mass-log-glyph">{e.result === 'ok' ? '✓' : e.result === 'error' ? '✕' : <Spinner />}</span>
                <span className="mass-log-name">{e.name} <span className="raid-acct-handle">@{e.username}</span></span>
                <span className="mass-log-act">{e.result === 'running' ? 'waiting…' : `${e.action}${e.result === 'ok' ? 'ed' : ''}`}</span>
                {e.message && <span className="mass-log-msg">{e.message}</span>}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* summary */}
      {stats && (
        <section className="px-card">
          <div className="px-section-head">summary <span className="px-line" /> <span className="px-count">mass raid completed</span></div>
          <div className="mass-summary">
            {['like', 'retweet', 'reply'].map((k) => counts[k] > 0 && (
              <div className="mass-stat" key={k}>
                <span className="mass-stat-label">{ACT_LABEL[k]}</span>
                <span className={`mass-stat-val ${stats[k].fail ? '' : 'ok'}`}>{stats[k].ok} / {counts[k]}</span>
                <span className="mass-stat-sub">{stats[k].fail ? `${stats[k].fail} failed` : 'success'}</span>
              </div>
            ))}
            <div className="mass-stat"><span className="mass-stat-label">Duration</span><span className="mass-stat-val">{stats.duration ? fmtDur(stats.duration) : '—'}</span></div>
            <div className="mass-stat"><span className="mass-stat-label">Failed</span><span className="mass-stat-val">{totalFail}</span></div>
            <div className="mass-stat"><span className="mass-stat-label">Success rate</span><span className="mass-stat-val ok">{rate}%</span></div>
          </div>
        </section>
      )}
    </>
  );
}
