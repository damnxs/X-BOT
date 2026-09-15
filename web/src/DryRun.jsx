import { useEffect, useState } from 'react';
import { api } from './api.js';
import { useToast, Spinner, errorMsg } from './toast.jsx';

const STATUS_CLS = { success: 'ok', failed: 'err', running: 'running' };
const STATUS_LABEL = { success: 'Success', failed: 'Failed', running: 'Running' };
const ACT_LABEL = { like: 'Like', retweet: 'Retweet', reply: 'Reply', post: 'Post', search: 'Search' };

const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString('en-US', { hour12: false }) : '—';
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';

export default function DryRun() {
  const [runs, setRuns] = useState(null);
  const [selected, setSelected] = useState(null);
  const [report, setReport] = useState(null);
  const [steps, setSteps] = useState(null);
  const [selFilter, setSelFilter] = useState('all');
  const [actFilter, setActFilter] = useState('all');
  const [expandedStep, setExpandedStep] = useState(null);
  const toast = useToast();

  useEffect(() => {
    api.dryRuns().then(setRuns).catch((e) => toast.error(errorMsg(e)));
  }, [toast]);

  const selectRun = (runId) => {
    setSelected(runId);
    setReport(null); setSteps(null); setExpandedStep(null);
    Promise.all([api.dryRunReport(runId), api.dryRunSteps(runId)])
      .then(([rpt, stp]) => { setReport(rpt); setSteps(stp); })
      .catch((e) => toast.error(errorMsg(e)));
  };

  // all selectors across steps (for the table)
  const allSelectors = steps ? steps.flatMap((s) =>
    (s.selectors || []).map((sel) => ({ ...sel, action: s.action, step: s.step }))
  ).filter((s) => selFilter === 'all' || s.status === selFilter)
   .filter((s) => actFilter === 'all' || s.action === actFilter) : [];

  const errorSteps = steps ? steps.filter((s) => s.error) : [];
  const selRate = report ? (() => {
    const tested = Object.values(report.selectors_tested || {});
    const total = tested.reduce((s, v) => s + v.found + v.missing, 0);
    const found = tested.reduce((s, v) => s + v.found, 0);
    return total ? Math.round((found / total) * 100) : 100;
  })() : 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="prompt">
          <span className="p-user">dryrun</span>
          <span className="p-at">@</span>
          <span className="p-host">mhfcorp</span>
          <span className="p-sep">:</span>
          <span className="p-path">~/dryrun</span>
          <span className="p-end">#</span>
        </div>
        <div className="status">
          {runs && <span className="badge badge-idle">{runs.length} session{runs.length === 1 ? '' : 's'}</span>}
        </div>
      </header>

      <main className="dry-shell">
        {/* ---- left: runs list ---- */}
        <div className="dry-list">
          <div className="px-section-head">sessions<span className="px-line" /></div>
          {!runs ? (
            <div className="dry-skel"><Spinner /></div>
          ) : runs.length === 0 ? (
            <p className="muted dry-empty">no dry-run sessions yet. Toggle Dry Run in Settings and trigger an action.</p>
          ) : runs.map((r) => (
            <div key={r.run_id} className={`dry-run-item${selected === r.run_id ? ' active' : ''}`}
                 onClick={() => selectRun(r.run_id)}>
              <span className={`dry-run-dot ${STATUS_CLS[r.status] || ''}`} />
              <div className="dry-run-info">
                <span className="dry-run-acct">{r.account || '—'}</span>
                <span className="dry-run-id">{r.run_id}</span>
              </div>
              <span className={`dry-run-status ${STATUS_CLS[r.status] || ''}`}>{STATUS_LABEL[r.status] || r.status}</span>
              <span className="dry-run-ts">{fmtDate(r.ts)}</span>
            </div>
          ))}
        </div>

        {/* ---- right: details ---- */}
        <div className="dry-detail">
          {!selected ? (
            <p className="muted dry-empty">select a session to inspect.</p>
          ) : !report ? (
            <div className="dry-skel"><Spinner /></div>
          ) : (
            <>
              {/* summary cards */}
              <div className="dry-stats">
                <div className="dry-stat-card">
                  <span className="dry-stat-label">tweets scanned</span>
                  <span className="dry-stat-val">{report.tweets_scanned ?? 0}</span>
                </div>
                <div className="dry-stat-card">
                  <span className="dry-stat-label">actions simulated</span>
                  <span className="dry-stat-val">{Object.values(report.actions_simulated || {}).reduce((a, b) => a + b, 0)}</span>
                  <div className="dry-stat-breakdown">
                    {Object.entries(report.actions_simulated || {}).filter(([, v]) => v > 0)
                      .map(([k, v]) => <span key={k}>{ACT_LABEL[k] || k}: {v}</span>)}
                  </div>
                </div>
                <div className="dry-stat-card">
                  <span className="dry-stat-label">errors</span>
                  <span className={`dry-stat-val ${report.errors ? 'err' : 'ok'}`}>{report.errors ?? 0}</span>
                </div>
                <div className="dry-stat-card">
                  <span className="dry-stat-label">selector health</span>
                  <span className={`dry-stat-val ${selRate < 80 ? 'err' : 'ok'}`}>{selRate}%</span>
                </div>
              </div>

              {/* timeline */}
              <section className="px-card dry-card">
                <div className="px-section-head">execution timeline<span className="px-line" /><span className="px-count">{steps?.length || 0} steps</span></div>
                {steps && steps.length > 0 ? steps.map((s) => {
                  const open = expandedStep === s.step;
                  const hasErr = !!s.error;
                  return (
                    <div key={s.step} className={`dry-step ${hasErr ? 'err' : 'ok'}${open ? ' expanded' : ''}`}>
                      <div className="dry-step-head" onClick={() => setExpandedStep(open ? null : s.step)}>
                        <span className="dry-step-glyph">{hasErr ? '✕' : '✓'}</span>
                        <span className="dry-step-act">{ACT_LABEL[s.action] || s.action}</span>
                        {s.tweet && <span className="dry-step-tweet">@{s.tweet.author}</span>}
                        {s.keyword && <span className="dry-step-tweet">"{s.keyword}"</span>}
                        {s.results_count != null && <span className="dry-step-meta">{s.results_count} tweets</span>}
                        <span className="dry-step-ts">{fmtTime(s.ts)}</span>
                        <span className="dry-step-caret">{open ? '▾' : '▸'}</span>
                      </div>
                      {open && (
                        <div className="dry-step-body">
                          <div className="dry-step-url muted">{s.url}</div>
                          {s.tweet && (
                            <div className="dry-step-tweet-info">
                              <span><b>@{s.tweet.author}</b></span>
                              <span className="dry-step-text">{s.tweet.text}</span>
                            </div>
                          )}
                          {s.selectors && s.selectors.length > 0 && (
                            <div className="dry-step-selectors">
                              {s.selectors.map((sel, i) => (
                                <div key={i} className={`dry-sel-row ${sel.status === 'FOUND' ? 'ok' : (sel.status === 'MISSING' ? 'err' : '')}`}>
                                  <span className="dry-sel-status">{sel.status === 'FOUND' ? '✓' : sel.status === 'MISSING' ? '✕' : '◌'}</span>
                                  <code>{sel.css}</code>
                                  {sel.xpath && <span className="muted dry-sel-xpath">{sel.xpath}</span>}
                                  {sel.attrs && Object.keys(sel.attrs).length > 0 && (
                                    <span className="dry-sel-attrs">{Object.entries(sel.attrs).map(([k, v]) => `${k}="${v}"`).join(' · ')}</span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                          {s.screenshot && (() => {
                            const fname = s.screenshot.split('/').pop();
                            return <img className="dry-step-shot" loading="lazy" src={api.dryRunScreenshot(selected, fname)} alt={`step ${s.step}`} />;
                          })()}
                          {s.error && (
                            <div className="dry-step-error">
                              <span className="err">{s.error.type}: {s.error.message}</span>
                              {s.error.traceback && <pre className="dry-traceback">{s.error.traceback}</pre>}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                }) : <p className="muted">no steps recorded.</p>}
              </section>

              {/* selector table */}
              <section className="px-card dry-card">
                <div className="px-section-head">
                  selector debug<span className="px-line" />
                  <div className="dry-filters">
                    {['all', 'FOUND', 'MISSING'].map((f) => (
                      <button key={f} className={`chip${selFilter === f ? ' on' : ''}`} onClick={() => setSelFilter(f)}>{f}</button>
                    ))}
                    <span className="raid-chip-label">action</span>
                    {['all', 'like', 'retweet', 'reply', 'search'].map((f) => (
                      <button key={f} className={`chip${actFilter === f ? ' on' : ''}`} onClick={() => setActFilter(f)}>{f}</button>
                    ))}
                  </div>
                </div>
                <div className="table-wrap">
                  <table className="dsh-table">
                    <thead><tr><th>Action</th><th>Selector</th><th>XPath</th><th>Status</th><th>Confidence</th><th>Attributes</th></tr></thead>
                    <tbody>
                      {allSelectors.length === 0 ? (
                        <tr><td colSpan={6} className="muted">no selectors match the filters.</td></tr>
                      ) : allSelectors.map((s, i) => (
                        <tr key={i}>
                          <td>{ACT_LABEL[s.action] || s.action}</td>
                          <td><code>{s.css}</code></td>
                          <td className="muted">{s.xpath || '—'}</td>
                          <td className={s.status === 'FOUND' ? 'ok' : (s.status === 'MISSING' ? 'err' : '')}>{s.status}</td>
                          <td className="muted">{s.confidence || '—'}</td>
                          <td>{s.attrs && Object.keys(s.attrs).length > 0
                            ? <span className="dry-attrs">{Object.entries(s.attrs).map(([k, v]) => `${k}="${v}"`).join(' · ')}</span>
                            : <span className="muted">—</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* error screenshots */}
              {errorSteps.length > 0 && (
                <section className="px-card dry-card">
                  <div className="px-section-head">error screenshots<span className="px-line" /><span className="px-count">{errorSteps.length}</span></div>
                  <div className="dry-gallery">
                    {errorSteps.map((s) => {
                      const fname = (s.screenshot || '').split('/').pop();
                      return (
                        <div key={s.step} className="dry-gallery-card">
                          {fname && <img loading="lazy" src={api.dryRunScreenshot(selected, fname)} alt={`step ${s.step}`} />}
                          <div className="dry-gallery-info">
                            <span>step {s.step}</span>
                            <span className={s.error ? 'err' : ''}>{s.error?.type || ACT_LABEL[s.action] || s.action}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}
