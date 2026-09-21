import { useEffect, useState } from 'react';
import { api } from './api.js';
import { useActions, useToast, Spinner } from './toast.jsx';

const boolVal = (v) => v === true || v === 'true' || v === '1';
const num = (v) => (v === '' || v == null ? NaN : +v);

function Section({ title, desc, children }) {
  return (
    <section className="set-card">
      <div className="set-head">
        <div>
          <h3 className="set-title">{title}</h3>
          {desc && <p className="set-desc">{desc}</p>}
        </div>
      </div>
      <div className="set-body">{children}</div>
    </section>
  );
}

function Field({ label, hint, error, children }) {
  return (
    <label className="set-field">
      <span className="set-field-label">{label}</span>
      {children}
      {error ? <span className="set-field-err">{error}</span>
        : (hint ? <span className="set-field-hint">{hint}</span> : null)}
    </label>
  );
}

function Toggle({ checked, onChange, label, desc }) {
  return (
    <label className="set-toggle">
      <span className="set-toggle-text">
        <span className="set-toggle-label">{label}</span>
        {desc && <span className="set-toggle-desc">{desc}</span>}
      </span>
      <span className="switch">
        <input type="checkbox" checked={checked} onChange={onChange} />
        <span className="switch-track" />
      </span>
    </label>
  );
}

export default function Settings() {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState(null);
  const { act, has } = useActions();
  const toast = useToast();

  useEffect(() => {
    api.settings().then((out) => { const s0 = { ...out, openai_api_key: '' }; setS(s0); setSaved(s0); })
      .catch((e) => toast.error(String((e && e.message) || e)));
  }, [toast]);

  if (!s) return <p className="muted">Loading…</p>;

  const set = (k, v) => setS((p) => ({ ...p, [k]: v }));

  // inline validation (ranges). Save is blocked while any of these are wrong.
  const errs = {};
  if (!(num(s.day_start_hour) >= 0 && num(s.day_start_hour) <= 24)) errs.day_start_hour = 'Enter 0–24';
  if (!(num(s.day_end_hour) >= 0 && num(s.day_end_hour) <= 24)) errs.day_end_hour = 'Enter 0–24';
  if (!(num(s.like_probability) >= 0 && num(s.like_probability) <= 1)) errs.like_probability = '0 – 1';
  if (!(num(s.retweet_probability) >= 0 && num(s.retweet_probability) <= 1)) errs.retweet_probability = '0 – 1';
  if (!(num(s.min_delay_seconds) >= 0)) errs.min_delay_seconds = 'Must be ≥ 0';
  if (!(num(s.max_delay_seconds) >= 0)) errs.max_delay_seconds = 'Must be ≥ 0';
  if (!(num(s.raid_step_gap) >= 0)) errs.raid_step_gap = 'Must be ≥ 0';
  const hasErrs = Object.keys(errs).length > 0;

  const dirty = saved ? JSON.stringify(s) !== JSON.stringify(saved) : false;

  const save = () => act('save', async () => {
    const body = {
      schedule_active: boolVal(s.schedule_active),
      day_start_hour: +s.day_start_hour,
      day_end_hour: +s.day_end_hour,
      reply_min_likes: +s.reply_min_likes,
      dry_run: boolVal(s.dry_run),
      headless: boolVal(s.headless),
      like_probability: +s.like_probability,
      retweet_probability: +s.retweet_probability,
      min_delay_seconds: +s.min_delay_seconds,
      max_delay_seconds: +s.max_delay_seconds,
      raid_independent: boolVal(s.raid_independent),
      raid_step_gap: +s.raid_step_gap || 0,
      openai_model: s.openai_model,
      openai_post_system_prompt: s.openai_post_system_prompt,
      openai_reply_system_prompt: s.openai_reply_system_prompt,
    };
    if (s.openai_api_key) body.openai_api_key = s.openai_api_key;
    const out = await api.updateSettings(body);
    const s0 = { ...out, openai_api_key: '' };
    setS(s0); setSaved(s0);
    toast.success('settings saved');
  });
  const runSchedule = () => act('tick', async () => {
    await api.triggerTick();
    toast.success('schedule round queued');
  });

  return (
    <div className="set">
      <header className="dash-head">
        <div className="dash-head-text">
          <h1 className="dash-title">Settings</h1>
          <p className="dash-sub">Control automation, engagement, and AI behavior.</p>
        </div>
      </header>

      <div className="set-grid">
        <Section title="Automation" desc="When and how the scheduler runs for active accounts.">
          <Toggle checked={boolVal(s.schedule_active)} onChange={(e) => set('schedule_active', e.target.checked)}
                  label="Schedule active" desc="Auto-run actions on an interval within the day window." />
          <Toggle checked={boolVal(s.dry_run)} onChange={(e) => set('dry_run', e.target.checked)}
                  label="Dry run" desc="Simulate everything except the final click — nothing is actually posted or engaged." />
          <Toggle checked={boolVal(s.headless)} onChange={(e) => set('headless', e.target.checked)}
                  label="Headless browser" desc="Run Chromium without a visible window." />
          <div className="set-row">
            <Field label="Day window start" hint="Hour 0–24, Jakarta time." error={errs.day_start_hour}>
              <input type="number" min="0" max="24" value={s.day_start_hour} onChange={(e) => set('day_start_hour', e.target.value)} />
            </Field>
            <Field label="Day window end" hint="Hour 0–24 (24 = midnight)." error={errs.day_end_hour}>
              <input type="number" min="0" max="24" value={s.day_end_hour} onChange={(e) => set('day_end_hour', e.target.value)} />
            </Field>
          </div>
        </Section>

        <Section title="Engagement" desc="Probabilities, pacing, and reply targeting.">
          <div className="set-row set-row-3">
            <Field label="Like probability" hint="0 – 1" error={errs.like_probability}>
              <input type="number" min="0" max="1" step="0.05" value={s.like_probability} onChange={(e) => set('like_probability', e.target.value)} />
            </Field>
            <Field label="Retweet probability" hint="0 – 1" error={errs.retweet_probability}>
              <input type="number" min="0" max="1" step="0.05" value={s.retweet_probability} onChange={(e) => set('retweet_probability', e.target.value)} />
            </Field>
            <Field label="Min delay (s)" hint="Between actions in a run." error={errs.min_delay_seconds}>
              <input type="number" min="0" value={s.min_delay_seconds} onChange={(e) => set('min_delay_seconds', e.target.value)} />
            </Field>
            <Field label="Max delay (s)" error={errs.max_delay_seconds}>
              <input type="number" min="0" value={s.max_delay_seconds} onChange={(e) => set('max_delay_seconds', e.target.value)} />
            </Field>
            <Field label="Reply · min likes" hint="">
              <input type="number" min="0" value={s.reply_min_likes} onChange={(e) => set('reply_min_likes', e.target.value)} />
            </Field>
          </div>
        </Section>

        <Section title="Raider" desc="Raid execution vs the warm-up scheduler. Human-delay pacing uses the min/max delay above — same logic as warm-up.">
          <Toggle checked={boolVal(s.raid_independent)} onChange={(e) => set('raid_independent', e.target.checked)}
                  label="Independent raids" desc="Raids run immediately in parallel — warm-up keeps running. If both hit the same account, that account's warm-up action slides ~12 min. Off = a raid waits for the account to be idle." />
          <div className="set-row">
            <Field label="Step gap (s)" hint="Extra randomized pause (±50%) between raid steps across accounts. 0 = back-to-back." error={errs.raid_step_gap}>
              <input type="number" min="0" value={s.raid_step_gap} onChange={(e) => set('raid_step_gap', e.target.value)} />
            </Field>
          </div>
        </Section>
      </div>

      <Section title="OpenAI" desc="Optional. Required to generate posts and replies. Leave the key blank to keep the saved one.">
        <div className="set-row">
          <Field label="Model" hint="e.g. gpt-4o-mini">
            <input value={s.openai_model} placeholder="gpt-4o-mini" onChange={(e) => set('openai_model', e.target.value)} />
          </Field>
          <Field label={s.openai_api_key_set ? 'API key (set — blank to keep)' : 'API key (not set)'} hint={s.openai_api_key_set ? 'A key is saved. Blank = no change.' : 'No key yet — posting & replies disabled.'}>
            <input type="password" value={s.openai_api_key || ''} placeholder="sk-..." onChange={(e) => set('openai_api_key', e.target.value)} />
          </Field>
        </div>
        <Field label="System prompt · Post" hint="Tone & rules for generated posts.">
          <textarea rows={3} value={s.openai_post_system_prompt} onChange={(e) => set('openai_post_system_prompt', e.target.value)} />
        </Field>
        <Field label="System prompt · Reply" hint="Tone & rules for generated replies.">
          <textarea rows={3} value={s.openai_reply_system_prompt} onChange={(e) => set('openai_reply_system_prompt', e.target.value)} />
        </Field>
      </Section>

      <div className="set-savebar">
        {dirty ? <span className="set-dirty">unsaved changes</span> : <span className="set-saved">all changes saved</span>}
        <button className="btn" disabled={has('tick')} onClick={runSchedule}>
          {has('tick') && <Spinner />} run schedule
        </button>
        <button className="btn btn-primary" disabled={!dirty || hasErrs || has('save')} onClick={save}>
          {has('save') && <Spinner />} {has('save') ? 'saving…' : 'save changes'}
        </button>
      </div>
    </div>
  );
}
