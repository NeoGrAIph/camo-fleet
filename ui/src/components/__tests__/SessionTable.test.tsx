import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { SessionItem } from '../../api';
import { SessionTable } from '../SessionTable';

const baseSession: SessionItem = {
  worker: 'alpha',
  id: 'session-1',
  status: 'READY',
  created_at: new Date('2024-01-01T00:00:00Z').toISOString(),
  last_seen_at: new Date().toISOString(),
  headless: true,
  idle_ttl_seconds: 120,
  labels: {},
  ws_endpoint: 'ws://example',
  vnc_enabled: false,
  vnc: {},
  start_url_wait: 'load',
};

describe('SessionTable', () => {
  it('renders empty state when there are no sessions', () => {
    render(
      <SessionTable sessions={[]} selectedKey={null} onSelect={vi.fn()} now={Date.now()} />,
    );

    expect(
      screen.getByText('There are no sessions yet. Launch one to get started.'),
    ).toBeInTheDocument();
  });

  it('invokes onSelect when a session row is clicked', () => {
    const onSelect = vi.fn();
    render(
      <SessionTable
        sessions={[baseSession]}
        selectedKey={null}
        onSelect={onSelect}
        now={Date.now()}
      />,
    );

    fireEvent.click(screen.getByText(baseSession.id));
    expect(onSelect).toHaveBeenCalledWith(`${baseSession.worker}:${baseSession.id}`);
  });
});
