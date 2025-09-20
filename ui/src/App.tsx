import { useEffect, useMemo, useState } from 'react';
import type { SessionItem, WorkerStatus } from './api';
import { createSession, fetchSessions, fetchWorkers } from './api';

interface CreateFormState {
  worker?: string;
  browser: string;
  headless: boolean;
  idle: number;
}

const DEFAULT_FORM: CreateFormState = {
  worker: undefined,
  browser: 'chromium',
  headless: false,
  idle: 300,
};

function formatRelative(date: string): string {
  const delta = Date.now() - new Date(date).valueOf();
  if (delta < 1000 * 60) return 'seconds ago';
  if (delta < 1000 * 60 * 60) return `${Math.floor(delta / 60000)} min ago`;
  return new Date(date).toLocaleString();
}

function statusClass(status: string): string {
  switch (status) {
    case 'READY':
      return 'status-ready';
    case 'DEAD':
      return 'status-dead';
    default:
      return '';
  }
}

export default function App(): JSX.Element {
  const [workers, setWorkers] = useState<WorkerStatus[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [form, setForm] = useState<CreateFormState>(DEFAULT_FORM);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const healthyWorkers = useMemo(
    () => workers.filter((worker) => worker.healthy),
    [workers],
  );

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
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const created = await createSession({
        worker: form.worker || undefined,
        browser: form.browser,
        headless: form.headless,
        idle_ttl_seconds: form.idle,
      });
      setSessions((prev) => [
        created,
        ...prev.filter((item) => item.worker !== created.worker || item.id !== created.id),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container">
      <h1>Camofleet dashboard</h1>
      {error && <div className="card status-dead">{error}</div>}

      <section className="card">
        <h2>Workers</h2>
        <ul>
          {workers.map((worker) => (
            <li key={worker.name}>
              <span className={worker.healthy ? 'status-ready' : 'status-dead'}>
                {worker.name}
              </span>{' '}
              — {worker.healthy ? 'healthy' : 'unreachable'}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Start browser session</h2>
        <form onSubmit={handleSubmit} className="form">
          <div>
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
                {healthyWorkers.map((worker) => (
                  <option key={worker.name} value={worker.name}>
                    {worker.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div>
            <label>
              Browser
              <select
                value={form.browser}
                onChange={(event) => setForm((prev) => ({ ...prev, browser: event.target.value }))}
              >
                <option value="chromium">Chromium</option>
                <option value="firefox">Firefox</option>
                <option value="webkit">Webkit</option>
              </select>
            </label>
          </div>
          <div>
            <label>
              Headless
              <input
                type="checkbox"
                checked={form.headless}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, headless: event.target.checked }))
                }
              />
            </label>
          </div>
          <div>
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
              />
            </label>
          </div>
          <button type="submit" disabled={loading}>
            {loading ? 'Launching…' : 'Launch session'}
          </button>
        </form>
      </section>

      <section className="card">
        <h2>Active sessions</h2>
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Status</th>
              <th>Worker</th>
              <th>Browser</th>
              <th>Last seen</th>
              <th>WebSocket</th>
              <th>VNC</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr key={`${session.worker}-${session.id}`}>
                <td>{session.id}</td>
                <td className={statusClass(session.status)}>{session.status}</td>
                <td>{session.worker}</td>
                <td>
                  {session.browser} {session.headless ? <span className="badge">headless</span> : null}
                </td>
                <td>{formatRelative(session.last_seen_at)}</td>
                <td>
                  <a href={session.ws_endpoint} target="_blank" rel="noreferrer">
                    connect
                  </a>
                </td>
                <td>
                  {session.vnc?.http ? (
                    <a href={session.vnc.http ?? undefined} target="_blank" rel="noreferrer">
                      Live view
                    </a>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
