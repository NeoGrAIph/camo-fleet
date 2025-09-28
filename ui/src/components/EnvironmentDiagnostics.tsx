import type { DiagnosticsReport, DiagnosticsTarget } from '../api';

interface EnvironmentDiagnosticsProps {
  report: DiagnosticsReport | null;
  loading: boolean;
  error: string | null;
  onRun: () => void;
}

function formatStatusLabel(status: string): string {
  const trimmed = status.trim();
  if (!trimmed) {
    return 'Unknown';
  }
  return trimmed
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function statusModifier(status: string): string {
  const normalised = status.toLowerCase();
  if (normalised === 'ok') return 'ok';
  if (normalised === 'skipped') return 'skipped';
  if (normalised === 'error') return 'error';
  return 'unknown';
}

function renderTargets(targets: DiagnosticsTarget[]): JSX.Element {
  if (!targets.length) {
    return <p className="diagnostics-empty">No diagnostics targets reported.</p>;
  }

  return (
    <div className="diagnostics-targets">
      {targets.map((target) => (
        <article key={target.url} className="diagnostics-target">
          <header className="diagnostics-target-header">
            <code>{target.url}</code>
          </header>
          <ul className="diagnostics-probes">
            {target.probes.map((probe) => (
              <li
                key={probe.protocol}
                className={`diagnostics-probe diagnostics-probe--${statusModifier(probe.status)}`}
              >
                <span className="diagnostics-protocol">{probe.protocol.toUpperCase()}</span>
                <span className="diagnostics-status">{formatStatusLabel(probe.status)}</span>
                {probe.detail ? <span className="diagnostics-detail">{probe.detail}</span> : null}
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

export function EnvironmentDiagnostics({
  report,
  loading,
  error,
  onRun,
}: EnvironmentDiagnosticsProps): JSX.Element {
  const generatedAt = report ? new Date(report.generated_at) : null;
  const hasResults = Boolean(report && report.workers.length);

  return (
    <section className="panel diagnostics-panel">
      <header className="panel-header diagnostics-panel-header">
        <div>
          <h2>Environment diagnostics</h2>
          <p>
            Run protocol probes (HTTP/2 and HTTP/3) from each runner to compare network
            capabilities between environments.
          </p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={onRun} disabled={loading}>
          {loading ? 'Running…' : 'Run diagnostics'}
        </button>
      </header>

      {error ? <p className="diagnostics-error">{error}</p> : null}

      {loading && !hasResults ? (
        <p className="diagnostics-message">Collecting diagnostics…</p>
      ) : null}

      {hasResults ? (
        <div className="diagnostics-report">
          {report!.workers.map((worker) => (
            <article key={worker.name} className="diagnostics-worker">
              <header className="diagnostics-worker-header">
                <div>
                  <h3>{worker.name}</h3>
                  <div className="diagnostics-meta">
                    <span>
                      Status: {formatStatusLabel(worker.diagnostics_status)}
                      {!worker.healthy ? ' · Worker unreachable' : ''}
                    </span>
                    {Object.entries(worker.checks).map(([checkName, checkStatus]) => (
                      <span key={checkName} className="diagnostics-check">
                        {checkName}: {formatStatusLabel(checkStatus)}
                      </span>
                    ))}
                  </div>
                </div>
              </header>
              {worker.notes.length ? (
                <ul className="diagnostics-notes">
                  {worker.notes.map((note, index) => (
                    <li key={index}>{note}</li>
                  ))}
                </ul>
              ) : null}
              {renderTargets(worker.targets)}
            </article>
          ))}
        </div>
      ) : !loading ? (
        <p className="diagnostics-message">
          Trigger diagnostics to capture a report highlighting protocol support from each
          runner.
        </p>
      ) : null}

      {generatedAt ? (
        <footer className="diagnostics-footer">
          Last run: {generatedAt.toLocaleString()}
        </footer>
      ) : null}
    </section>
  );
}
