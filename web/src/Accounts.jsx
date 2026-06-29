import { useEffect, useState } from 'react';
import { api } from './api.js';

const empty = {
  name: '', username: '', auth_token: '', ct0: '', keywords: '',
  mode: 'search', daily_posts: 0, daily_likes: 5, daily_retweets: 2, daily_replies: 0,
  like_probability: 0.6, retweet_probability: 0.4, active: true,
};

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [today, setToday] = useState({});
  const [editing, setEditing] = useState(null);
  const [logsId, setLogsId] = useState(null);
  const [schedId, setSchedId] = useState(null);
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
        <button onClick={() => setEditing({ ...empty })}>+ Add account</button>
        {err && <span className="err">{err}</span>}
      </div>
      <table>
        <thead>
          <tr><th>Name</th><th>@</th><th>Mode</th><th>Quota P/L/RT/Rp</th><th>Done today</th><th>Active</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.id}>
              <td>{a.name}</td>
              <td>{a.username}</td>
              <td>{a.mode}</td>
              <td>{a.daily_posts}/{a.daily_likes}/{a.daily_retweets}/{a.daily_replies ?? 0}</td>
              <td>{today[a.id] ? `${today[a.id].done.posts}/${today[a.id].done.likes}/${today[a.id].done.retweets}/${today[a.id].done.replies ?? 0}` : '-'}</td>
              <td>
                <input type="checkbox" checked={a.active} onChange={async (e) => { await api.updateAccount(a.id, { active: e.target.checked }); load(); }} />
              </td>
              <td className="actions">
                <button onClick={() => api.runNow(a.id).then((r) => { if (!r.queued) alert(r.msg || 'nothing to run'); setTimeout(load, 800); })}>Run now</button>
                <button onClick={() => setSchedId(a.id)}>Schedule</button>
                <button onClick={() => api.replan(a.id).then((r) => { alert((r.remaining || 0) + ' actions remaining — chain restarted'); load(); })}>Replan</button>
                <button onClick={() => setLogsId(a.id)}>Logs</button>
                <button onClick={() => setEditing({ ...a, keywords: (a.keywords || []).join(', ') })}>Edit</button>
                <button onClick={() => { if (confirm('Delete ' + a.name + '?')) api.deleteAccount(a.id).then(load); }}>Del</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
  const set = (k, v) => setA((p) => ({ ...p, [k]: v }));
  const num = (k) => (e) => set(k, e.target.value);
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
        <label className="check"><input type="checkbox" checked={a.active} onChange={(e) => set('active', e.target.checked)} /> Active</label>
        <div className="row">
          <button onClick={() => onSave(a)}>{value.id ? 'Save' : 'Create'}</button>
          <button onClick={onCancel}>Cancel</button>
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
        <div className="row"><h3>Account #{accountId} — runs</h3><button onClick={onClose}>Close</button></div>
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
        <h4>actions.jsonl (tail — paste back to debug)</h4>
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
  const fmt = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return (
    <div className="modal">
      <div className="modal-card">
        <div className="row"><h3>Account #{accountId} — today's plan</h3><button onClick={onClose}>Close</button></div>
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
