import { useEffect, useMemo, useState } from 'react';
import type { SessionItem, StartUrlWait, WorkerStatus } from './api';
import {
  createSession,
  deleteSession,
  fetchSessions,
  fetchWorkers,
  touchSession,
} from './api';

interface CreateFormState {
  worker?: string;
  headless: boolean;
  idle: number;
  startUrl: string;
  labels: string;
  vnc: boolean;
  startUrlWait: StartUrlWait;
}

type ThemeMode = 'light' | 'dark';

type ActionState = {
  type: 'kill' | 'touch';
  key: string;
} | null;

const DEFAULT_FORM: CreateFormState = {
  worker: undefined,
  headless: false,
  idle: 300,
  startUrl: '',
  labels: '',
  vnc: false,
  startUrlWait: 'load',
};

const START_URL_WAIT_OPTIONS: StartUrlWait[] = ['load', 'domcontentloaded', 'none'];

const THEME_STORAGE_KEY = 'camofleet-ui-theme';

function sessionKey(session: Pick<SessionItem, 'worker' | 'id'>): string {
  return `${session.worker}:${session.id}`;
}

function formatRelative(date: string): string {
  const target = new Date(date).valueOf();
  if (Number.isNaN(target)) return 'unknown';
  const delta = Date.now() - target;
  if (delta < 30_000) return 'just now';
  if (delta < 60_000) return `${Math.round(delta / 1000)}s ago`;
  if (delta < 3_600_000) return `${Math.round(delta / 60_000)}m ago`;
  if (delta < 43_200_000) return `${Math.round(delta / 3_600_000)}h ago`;
  return new Date(date).toLocaleString();
}

function formatIdle(seconds: number): string {
  if (!Number.isFinite(seconds)) return '—';
  const clamped = Math.max(0, Math.floor(seconds));
  return `${clamped}s`;
}

function formatStartUrlWait(mode: StartUrlWait | undefined | null): string {
  switch (mode) {
    case 'none':
      return 'No wait';
    case 'domcontentloaded':
      return 'DOM ready';
    case 'load':
    default:
      return 'Full load';
  }
}

function remainingIdleSeconds(
  session: Pick<SessionItem, 'last_seen_at' | 'idle_ttl_seconds'>,
  nowMs: number,
): number {
  const lastSeen = new Date(session.last_seen_at).valueOf();
  if (Number.isNaN(lastSeen)) {
    return Math.max(0, Math.floor(session.idle_ttl_seconds));
  }
  const elapsedSeconds = Math.max(0, Math.floor((nowMs - lastSeen) / 1000));
  const remaining = Math.floor(session.idle_ttl_seconds - elapsedSeconds);
  return remaining > 0 ? remaining : 0;
}

function statusBadge(status: string): string {
  switch (status) {
    case 'READY':
      return 'badge badge-ready';
    case 'INIT':
      return 'badge badge-warmup';
    case 'TERMINATING':
      return 'badge badge-warning';
    case 'DEAD':
      return 'badge badge-dead';
    default:
      return 'badge';
  }
}

function parseLabels(raw: string): Record<string, string> | undefined {
  const entries = raw
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .map((pair) => {
      const [key, ...rest] = pair.split('=');
      if (!key) return null;
      return [key.trim(), rest.join('=').trim()] as const;
    })
    .filter((entry): entry is readonly [string, string] => Boolean(entry && entry[0]));
  if (!entries.length) return undefined;
  return Object.fromEntries(entries);
}

function buildVncEmbedUrl(raw?: string | null): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    url.searchParams.set('autoconnect', '1');
    url.searchParams.set('resize', 'scale');
    url.searchParams.set('reconnect', 'true');
    return url.toString();
  } catch (error) {
    console.warn('Failed to build VNC URL', error);
    return raw;
  }
}

function getInitialTheme(): ThemeMode {
  if (typeof window === 'undefined') {
    return 'light';
  }
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null;
  if (stored === 'dark' || stored === 'light') return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export default function App(): JSX.Element {
  const [theme, setTheme] = useState<ThemeMode>(getInitialTheme);
  const [workers, setWorkers] = useState<WorkerStatus[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [form, setForm] = useState<CreateFormState>(DEFAULT_FORM);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionState, setActionState] = useState<ActionState>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    }
  }, [theme]);

  useEffect(() => {
    const load = async () => {
      try {
        const [workerData, sessionData] = await Promise.all([
          fetchWorkers(),
          fetchSessions(),
        ]);
        setWorkers(workerData);
        setSessions(sessionData);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    };
    load();
    const interval = window.setInterval(load, 5000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (selectedKey && !sessions.some((item) => sessionKey(item) === selectedKey)) {
      setSelectedKey(null);
    }
  }, [sessions, selectedKey]);

  const healthyWorkers = useMemo(
    () => workers.filter((worker) => worker.healthy),
    [workers],
  );

  const selectedSession = useMemo(
    () => sessions.find((item) => sessionKey(item) === selectedKey) ?? null,
    [sessions, selectedKey],
  );

  const selectedSessionKey = useMemo(
    () => (selectedSession ? sessionKey(selectedSession) : null),
    [selectedSession],
  );

  const stats = useMemo(() => {
    const summary = sessions.reduce(
      (acc, session) => {
        acc.total += 1;
        acc.byStatus.set(session.status, (acc.byStatus.get(session.status) ?? 0) + 1);
        return acc;
      },
      { total: 0, byStatus: new Map<string, number>() },
    );
    return summary;
  }, [sessions]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const labels = parseLabels(form.labels);
      const payload = {
        worker: form.worker || undefined,
        headless: form.headless,
        idle_ttl_seconds: form.idle,
        start_url: form.startUrl || undefined,
        start_url_wait: form.startUrlWait,
        labels,
        vnc: form.vnc,
      };
      const created = await createSession(payload);
      setSessions((prev) => [
        created,
        ...prev.filter((item) => sessionKey(item) !== sessionKey(created)),
      ]);
      setSelectedKey(sessionKey(created));
      setForm((prev) => ({ ...prev, startUrl: '', labels: '' }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleKill = async (session: SessionItem) => {
    const key = sessionKey(session);
    setActionState({ type: 'kill', key });
    setError(null);
    try {
      await deleteSession(session.worker, session.id);
      setSessions((prev) => prev.filter((item) => sessionKey(item) !== key));
      if (selectedKey === key) {
        setSelectedKey(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionState(null);
    }
  };

  const handleTouch = async (session: SessionItem) => {
    const key = sessionKey(session);
    setActionState({ type: 'touch', key });
    setError(null);
    try {
      const refreshed = await touchSession(session.worker, session.id);
      setSessions((prev) => [
        refreshed,
        ...prev.filter((item) => sessionKey(item) !== key),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionState(null);
    }
  };

  const actionInFlight = (type: ActionState['type'], session: SessionItem | null) => {
    if (!actionState || !session) return false;
    return actionState.type === type && actionState.key === sessionKey(session);
  };

  const onThemeToggle = () => setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));

  const handleCopyWs = (endpoint: string) => {
    if (!navigator.clipboard) {
      setError('Clipboard API is not available in this browser.');
      return;
    }
    navigator.clipboard
      .writeText(endpoint)
      .then(() => setError(null))
      .catch((err) =>
        setError(err instanceof Error ? `Failed to copy: ${err.message}` : 'Failed to copy endpoint'),
      );
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <header className="sidebar-header">
          <div>
            <h1>Selenoid UI v2</h1>
            <p className="subtitle">Camofleet control panel</p>
          </div>
          <button className="btn btn-ghost" type="button" onClick={onThemeToggle}>
            {theme === 'dark' ? '☀️ Light mode' : '🌙 Dark mode'}
          </button>
        </header>

        <section className="sidebar-section">
          <h2>Cluster status</h2>
          <ul className="worker-list">
            {workers.map((worker) => (
              <li key={worker.name}>
                <span className={worker.healthy ? 'pill pill-healthy' : 'pill pill-offline'}>
                  {worker.healthy ? '●' : '○'}
                </span>
                <div>
                  <strong>{worker.name}</strong>
                  <small>
                    {worker.healthy ? 'Healthy' : 'Unreachable'} ·
                    {worker.supports_vnc ? ' VNC' : ' No VNC'}
                  </small>
                </div>
              </li>
            ))}
            {!workers.length && <li className="empty">No workers discovered</li>}
          </ul>
        </section>

        <section className="sidebar-section">
          <h2>Launch session</h2>
          <form className="launch-form" onSubmit={handleSubmit}>
            <label>
              Worker
              <select
                value={form.worker ?? ''}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    worker: event.target.value || undefined,
                  }))
                }
              >
                <option value="">Auto</option>
                {healthyWorkers
                  .filter((worker) => !form.vnc || worker.supports_vnc)
                  .map((worker) => (
                    <option key={worker.name} value={worker.name}>
                      {worker.name}
                    </option>
                  ))}
              </select>
              {form.vnc && healthyWorkers.every((worker) => !worker.supports_vnc) ? (
                <span className="form-hint">Workers with VNC support are unavailable.</span>
              ) : null}
            </label>

            <div className="launch-row">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.headless}
                  disabled={form.vnc}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, headless: event.target.checked }))
                  }
                />
                Headless
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={form.vnc}
                  onChange={(event) =>
                    setForm((prev) => ({
                      ...prev,
                      vnc: event.target.checked,
                      headless: event.target.checked ? false : prev.headless,
                      worker: event.target.checked ? undefined : prev.worker,
                    }))
                  }
                />
                Enable VNC
              </label>
            </div>

            <label>
              Idle TTL (seconds)
              <input
                type="number"
                min={30}
                max={3600}
                value={form.idle}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, idle: Number(event.target.value) }))
                }
                required
              />
            </label>

            <label>
              Start URL (optional)
              <input
                type="url"
                placeholder="https://example.org"
                value={form.startUrl}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, startUrl: event.target.value }))
                }
              />
            </label>

            <label>
              Start URL wait
              <select
                value={form.startUrlWait}
                onChange={(event) =>
                  setForm((prev) => ({
                    ...prev,
                    startUrlWait: event.target.value as StartUrlWait,
                  }))
                }
              >
                {START_URL_WAIT_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {formatStartUrlWait(option)}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Labels (key=value, comma separated)
              <input
                type="text"
                placeholder="owner=qa, manual=true"
                value={form.labels}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, labels: event.target.value }))
                }
              />
            </label>

            <button className="btn btn-primary" type="submit" disabled={loading}>
              {loading ? 'Launching…' : 'Launch session'}
            </button>
            {error && <p className="form-error">{error}</p>}
          </form>
        </section>
      </aside>

      <main className="main">
        <section className="topbar">
          <div className="stat">
            <span className="stat-label">Total</span>
            <span className="stat-value">{stats.total}</span>
          </div>
          {Array.from(stats.byStatus.entries()).map(([status, count]) => (
            <div key={status} className="stat">
              <span className="stat-label">{status}</span>
              <span className="stat-value">{count}</span>
            </div>
          ))}
        </section>

        <div className="content-grid">
          <section className="panel">
            <header className="panel-header">
              <div>
                <h2>Sessions</h2>
                <p>{sessions.length ? 'Select a session to manage it' : 'No active sessions'}</p>
              </div>
            </header>
            <div className="table-wrapper">
              <table className="sessions-table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Worker</th>
                    <th>Session ID</th>
                    <th>Mode</th>
                    <th>Last seen</th>
                    <th>TTL left</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((session) => {
                    const key = sessionKey(session);
                    const startUrlWait = session.start_url_wait ?? 'load';
                    return (
                      <tr
                        key={key}
                        className={key === selectedKey ? 'selected' : ''}
                        onClick={() => setSelectedKey(key)}
                      >
                        <td>
                          <span className={statusBadge(session.status)}>{session.status}</span>
                        </td>
                        <td>{session.worker}</td>
                        <td className="mono">{session.id}</td>
                        <td className="table-mode">
                          <span className="pill pill-muted">Camoufox</span>
                          {session.headless ? <span className="pill pill-muted">headless</span> : null}
                          {session.vnc_enabled ? <span className="pill pill-muted">VNC</span> : null}
                          {startUrlWait !== 'load' ? (
                            <span className="pill pill-muted">{formatStartUrlWait(startUrlWait)}</span>
                          ) : null}
                        </td>
                        <td>{formatRelative(session.last_seen_at)}</td>
                        <td>{formatIdle(remainingIdleSeconds(session, now))}</td>
                      </tr>
                    );
                  })}
                  {!sessions.length && (
                    <tr>
                      <td colSpan={6} className="empty">
                        There are no sessions yet. Launch one to get started.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel">
            <header className="panel-header">
              <h2>Inspector</h2>
            </header>
            {selectedSession ? (
              <div className="session-details">
                <div className="details-header">
                  <div>
                    <h3 className="mono">{selectedSession.id}</h3>
                    <p>
                      Worker <strong>{selectedSession.worker}</strong> · Camoufox{' '}
                      {selectedSession.headless ? <span className="pill pill-muted">headless</span> : null}{' '}
                      {selectedSession.vnc_enabled ? <span className="pill pill-muted">VNC</span> : null}
                    </p>
                  </div>
                  <div className="actions">
                    <button
                      className="btn btn-secondary"
                      type="button"
                      onClick={() => handleTouch(selectedSession)}
                      disabled={actionInFlight('touch', selectedSession)}
                    >
                      {actionInFlight('touch', selectedSession) ? 'Extending…' : 'Extend TTL'}
                    </button>
                    <button
                      className="btn btn-danger"
                      type="button"
                      onClick={() => handleKill(selectedSession)}
                      disabled={actionInFlight('kill', selectedSession)}
                    >
                      {actionInFlight('kill', selectedSession) ? 'Terminating…' : 'Terminate'}
                    </button>
                  </div>
                </div>

                <dl className="details-list">
                  <div>
                    <dt>Status</dt>
                    <dd>
                      <span className={statusBadge(selectedSession.status)}>{selectedSession.status}</span>
                    </dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{new Date(selectedSession.created_at).toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>Last activity</dt>
                    <dd>{formatRelative(selectedSession.last_seen_at)}</dd>
                  </div>
                  <div>
                    <dt>TTL left</dt>
                    <dd>{formatIdle(remainingIdleSeconds(selectedSession, now))}</dd>
                  </div>
                  <div>
                    <dt>Start URL wait</dt>
                    <dd>{formatStartUrlWait(selectedSession.start_url_wait ?? 'load')}</dd>
                  </div>
                  <div>
                    <dt>WebSocket endpoint</dt>
                    <dd>
                      <button
                        className="btn btn-link"
                        type="button"
                        onClick={() => handleCopyWs(selectedSession.ws_endpoint)}
                      >
                        Copy
                      </button>
                      <code>{selectedSession.ws_endpoint}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Labels</dt>
                    <dd>
                      {Object.keys(selectedSession.labels || {}).length === 0 ? (
                        <span className="pill pill-muted">None</span>
                      ) : (
                        <div className="labels">
                          {Object.entries(selectedSession.labels).map(([key, value]) => (
                            <span key={key} className="pill pill-muted">
                              {key}: {value}
                            </span>
                          ))}
                        </div>
                      )}
                    </dd>
                  </div>
                </dl>

                <div className="vnc-wrapper">
                  <div className="vnc-header">
                    <h4>Live browser</h4>
                    <div className="actions">
                      {selectedSession.vnc?.http ? (
                        <a
                          className="btn btn-secondary"
                          href={selectedSession.vnc.http}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open full screen
                        </a>
                      ) : null}
                      {selectedSession.vnc?.ws ? (
                        <a
                          className="btn btn-secondary"
                          href={selectedSession.vnc.ws}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Raw VNC WS
                        </a>
                      ) : null}
                    </div>
                  </div>
                  {selectedSession.vnc?.http ? (
                    <iframe
                      title="Browser session"
                      key={selectedSessionKey ?? 'no-session'}
                      src={buildVncEmbedUrl(selectedSession.vnc.http) ?? undefined}
                      className="vnc-frame"
                    />
                  ) : (
                    <div className="empty">VNC is not available for this session.</div>
                  )}
                </div>
              </div>
            ) : (
              <div className="empty-state">
                <p>Select a session to inspect details, control TTL, or open the live browser.</p>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
