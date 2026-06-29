import { useEffect, useState } from 'react';
import { api } from './api';
import Accounts from './Accounts.jsx';
import Settings from './Settings.jsx';

export default function App() {
  const [tab, setTab] = useState('accounts');
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const tick = () => api.status().then(setStatus).catch(() => {});
    tick();
    const i = setInterval(tick, 3000);
    return () => clearInterval(i);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>X Bot</h1>
        <div className="status">
          {status ? (
            <>
              <span className={`dot ${status.running ? 'on' : ''}`} />
              {status.running
                ? `running ${status.current.name} (${status.current.trigger})`
                : 'idle'}
              {' · schedule ' + (status.schedule_active ? 'on' : 'off')}
              {' · today ' + status.today_done + '/' + (status.today_done + status.today_pending)}
              {' · queue ' + status.queue_depth}
              {status.next_tick && ' · next ' + new Date(status.next_tick).toLocaleTimeString()}
            </>
          ) : (
            '…'
          )}
        </div>
        <nav>
          <button className={tab === 'accounts' ? 'active' : ''} onClick={() => setTab('accounts')}>
            Accounts
          </button>
          <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}>
            Settings
          </button>
        </nav>
      </header>
      <main>{tab === 'accounts' ? <Accounts /> : <Settings />}</main>
    </div>
  );
}
