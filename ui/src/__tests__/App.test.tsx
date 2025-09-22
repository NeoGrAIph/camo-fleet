import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import type { SessionItem, WorkerStatus } from '../api';

const { fetchWorkersMock, fetchSessionsMock } = vi.hoisted(() => ({
  fetchWorkersMock: vi.fn<[], Promise<WorkerStatus[]>>(),
  fetchSessionsMock: vi.fn<[], Promise<SessionItem[]>>(),
}));

vi.mock('../api', () => ({
  fetchWorkers: fetchWorkersMock,
  fetchSessions: fetchSessionsMock,
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  touchSession: vi.fn(),
}));

describe('App polling behaviour', () => {
  const flushPromises = async () => {
    await act(async () => {
      await Promise.resolve();
    });
  };

  beforeAll(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  beforeEach(() => {
    vi.useFakeTimers();
    fetchWorkersMock.mockReset();
    fetchSessionsMock.mockReset();

    const workers: WorkerStatus[] = [
      { name: 'alpha', healthy: true, detail: {}, supports_vnc: true },
    ];
    const sessions: SessionItem[] = [];

    fetchWorkersMock.mockImplementationOnce(() => Promise.reject(new Error('Network down')));
    fetchSessionsMock.mockImplementationOnce(() => Promise.reject(new Error('Network down')));
    fetchWorkersMock.mockImplementation(() => Promise.resolve(workers));
    fetchSessionsMock.mockImplementation(() => Promise.resolve(sessions));
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('clears stale error after successful polling cycle', async () => {
    render(<App />);

    await flushPromises();
    expect(screen.getByText('Network down')).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    await flushPromises();

    expect(fetchWorkersMock).toHaveBeenCalledTimes(2);
    expect(fetchSessionsMock).toHaveBeenCalledTimes(2);

    await flushPromises();

    expect(screen.queryByText('Network down')).not.toBeInTheDocument();
  });

  it('resets worker selection when the chosen worker becomes unavailable', async () => {
    fetchWorkersMock.mockReset();
    fetchSessionsMock.mockReset();

    const workerResponses: WorkerStatus[][] = [
      [
        { name: 'alpha', healthy: true, detail: {}, supports_vnc: true },
        { name: 'beta', healthy: true, detail: {}, supports_vnc: true },
      ],
      [{ name: 'beta', healthy: true, detail: {}, supports_vnc: true }],
    ];
    let workersCallCount = 0;
    fetchWorkersMock.mockImplementation(() => {
      const index = Math.min(workersCallCount, workerResponses.length - 1);
      workersCallCount += 1;
      return Promise.resolve(workerResponses[index]);
    });
    fetchSessionsMock.mockResolvedValue([]);

    render(<App />);

    await flushPromises();

    const workerSelect = screen.getByLabelText('Worker') as HTMLSelectElement;
    expect(workerSelect).toHaveValue('');

    await act(async () => {
      fireEvent.change(workerSelect, { target: { value: 'alpha' } });
    });

    await flushPromises();
    expect(workerSelect).toHaveValue('alpha');

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    await flushPromises();

    expect(workerSelect).toHaveValue('');
    expect(screen.queryByRole('option', { name: 'alpha' })).not.toBeInTheDocument();
  });
});
