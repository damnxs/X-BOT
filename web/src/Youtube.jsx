import { useState } from 'react';

// Youtube Automation — layout scaffold. No backend yet, so every action surfaces
// an inline "coming soon" notice. When the module is built, replace the notices
// with real api calls and wire channels + the video queue to the server.
export default function Youtube() {
  const [notice, setNotice] = useState('');

  return (
    <div className="app">
      <header className="topbar">
        <div className="prompt">
          <span className="p-user">youtube</span>
          <span className="p-at">@</span>
          <span className="p-host">mhfcorp</span>
          <span className="p-sep">:</span>
          <span className="p-path">~/youtube</span>
          <span className="p-end">#</span>
        </div>
        <div className="status">
          <span className="badge badge-idle">not connected</span>
        </div>
      </header>

      <main>
        <div className="row">
          <button className="primary" onClick={() => setNotice('Video creation is coming soon.')}>
            + create video
          </button>
          <button onClick={() => setNotice('Channel connection is coming soon.')}>connect channel</button>
          {notice && (
            <span className="notice">
              <span className="notice-glyph">▶</span>{notice}
              <button className="notice-x" onClick={() => setNotice('')} aria-label="dismiss">✕</button>
            </span>
          )}
        </div>

        <h4>channels</h4>
        <div className="empty">no channels connected yet.</div>

        <h4>video queue</h4>
        <table>
          <thead>
            <tr><th>Title</th><th>Status</th><th>Scheduled</th><th>Views</th></tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={4} className="empty-cell">
                no videos yet — click <b>create video</b> to start.
              </td>
            </tr>
          </tbody>
        </table>
      </main>
    </div>
  );
}
