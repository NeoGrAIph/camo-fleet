import type { WorkerStatus } from '../api';

interface WorkerListProps {
  workers: WorkerStatus[];
}

export function WorkerList({ workers }: WorkerListProps): JSX.Element {
  if (!workers.length) {
    return (
      <ul className="worker-list">
        <li className="empty">No workers discovered</li>
      </ul>
    );
  }

  return (
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
    </ul>
  );
}
