import { useEffect, useState } from 'react';
import { api } from './api.js';

const boolVal = (v) => v === true || v === 'true' || v === '1';

function Field({ label, children }) {
  return <label>{label}{children}</label>;
}

export default function Settings() {
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { api.settings().then(setS); }, []);
  if (!s) return <p>Loading…</p>;

  const set = (k, v) => setS((p) => ({ ...p, [k]: v }));
  const save = async () => {
    const body = {
      schedule_active: boolVal(s.schedule_active),
      day_start_hour: +s.day_start_hour,
      day_end_hour: +s.day_end_hour,
      reply_min_likes: +s.reply_min_likes,
      reply_max_age_hours: +s.reply_max_age_hours,
      dry_run: boolVal(s.dry_run),
      headless: boolVal(s.headless),
      like_probability: +s.like_probability,
      retweet_probability: +s.retweet_probability,
      min_delay_seconds: +s.min_delay_seconds,
      max_delay_seconds: +s.max_delay_seconds,
      openai_model: s.openai_model,
      openai_system_prompt: s.openai_system_prompt,
    };
    if (s.openai_api_key) body.openai_api_key = s.openai_api_key;
    try {
      const out = await api.updateSettings(body);
      setS({ ...out, openai_api_key: '' });
      setMsg('saved');
    } catch (e) {
      setMsg(String(e));
    }
  };

  return (
    <div className="settings">
      <h3>Global settings</h3>
      <div className="grid2">
        <Field label="Day window start (hour 0-24)"><input value={s.day_start_hour} onChange={(e) => set('day_start_hour', e.target.value)} /></Field>
        <Field label="Day window end (hour 0-24)"><input value={s.day_end_hour} onChange={(e) => set('day_end_hour', e.target.value)} /></Field>
        <Field label="Reply: min likes (popularity)"><input value={s.reply_min_likes} onChange={(e) => set('reply_min_likes', e.target.value)} /></Field>
        <Field label="Reply: max age (hours, recency)"><input value={s.reply_max_age_hours} onChange={(e) => set('reply_max_age_hours', e.target.value)} /></Field>
        <Field label="Like probability"><input value={s.like_probability} onChange={(e) => set('like_probability', e.target.value)} /></Field>
        <Field label="Retweet probability"><input value={s.retweet_probability} onChange={(e) => set('retweet_probability', e.target.value)} /></Field>
        <Field label="Min delay (s)"><input value={s.min_delay_seconds} onChange={(e) => set('min_delay_seconds', e.target.value)} /></Field>
        <Field label="Max delay (s)"><input value={s.max_delay_seconds} onChange={(e) => set('max_delay_seconds', e.target.value)} /></Field>
        <label className="check"><input type="checkbox" checked={boolVal(s.dry_run)} onChange={(e) => set('dry_run', e.target.checked)} /> Dry run (engage nothing)</label>
        <label className="check"><input type="checkbox" checked={boolVal(s.headless)} onChange={(e) => set('headless', e.target.checked)} /> Headless browser</label>
        <label className="check"><input type="checkbox" checked={boolVal(s.schedule_active)} onChange={(e) => set('schedule_active', e.target.checked)} /> Schedule active (auto-run on interval)</label>
      </div>

      <h3>OpenAI (for posting — optional)</h3>
      <Field label="Model"><input value={s.openai_model} onChange={(e) => set('openai_model', e.target.value)} /></Field>
      <Field label="System prompt"><textarea rows={2} value={s.openai_system_prompt} onChange={(e) => set('openai_system_prompt', e.target.value)} /></Field>
      <Field label={s.openai_api_key_set ? 'API key (set — leave blank to keep)' : 'API key (not set — posts disabled)'}>
        <input type="password" value={s.openai_api_key || ''} placeholder="sk-..." onChange={(e) => set('openai_api_key', e.target.value)} />
      </Field>

      <div className="row">
        <button onClick={save}>Save settings</button>
        <button onClick={() => api.triggerTick().then(() => setMsg('schedule round queued')).catch((e) => setMsg(String(e)))}>Run schedule now</button>
        {msg && <span>{msg}</span>}
      </div>
    </div>
  );
}
