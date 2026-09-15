import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

// ---- error message extraction ------------------------------------------
// api.js throws `new Error(text)`; FastAPI HTTPExceptions come back as
// {"detail": "..."}. Unwrap both so toasts read cleanly.
export function errorMsg(e) {
  const raw = (e && (e.message || e.error)) || String(e);
  try {
    const j = JSON.parse(raw);
    if (j && j.detail != null) return typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
  } catch { /* not JSON — return raw */ }
  return raw;
}

// ---- toast context -----------------------------------------------------
const ToastCtx = createContext(null);

export function useToast() {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}

let _seq = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const timers = useRef({});

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
    if (timers.current[id]) { clearTimeout(timers.current[id]); delete timers.current[id]; }
  }, []);

  const push = useCallback((type, msg, ttl) => {
    const id = ++_seq;
    setToasts((t) => [...t.slice(-4), { id, type, msg }]); // keep at most ~5
    timers.current[id] = setTimeout(() => dismiss(id), ttl ?? (type === 'error' ? 6000 : 4000));
    return id;
  }, [dismiss]);

  const api = useMemo(() => ({
    success: (m, t) => push('success', m, t),
    error:   (m, t) => push('error', m, t),
    info:    (m, t) => push('info', m, t),
    dismiss,
  }), [push, dismiss]);

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="toast-wrap" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`} role="status">
            <span className="toast-icon">{t.type === 'success' ? '✓' : t.type === 'error' ? '✕' : '·'}</span>
            <span className="toast-msg">{t.msg}</span>
            <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="dismiss">×</button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// ---- async actions: loading + prevent-double + error toast -------------
// `act(key, fn)` guards against re-entry on the same key (so a button can't
// fire twice while in flight), runs fn, toasts any thrown error, and clears
// the busy flag in finally. The caller toasts success/info inside fn.
export function useActions() {
  const { error } = useToast();
  const [busy, setBusy] = useState(() => new Set());
  const busyRef = useRef(new Set());

  const mark = useCallback((k) => {
    busyRef.current.add(k); setBusy(new Set(busyRef.current));
  }, []);
  const clear = useCallback((k) => {
    busyRef.current.delete(k); setBusy(new Set(busyRef.current));
  }, []);

  const act = useCallback(async (key, fn) => {
    if (busyRef.current.has(key)) return null;   // prevent double-trigger
    mark(key);
    try {
      return await fn();
    } catch (e) {
      error(errorMsg(e));
      return null;
    } finally {
      clear(key);
    }
  }, [mark, clear, error]);

  const has = useCallback((k) => busyRef.current.has(k), []);
  // has() reads the ref so disabled buttons update immediately; `busy` state
  // is what triggers re-renders.
  return { act, has, busy };
}

export function Spinner() {
  return <span className="btn-spinner" aria-hidden="true" />;
}
