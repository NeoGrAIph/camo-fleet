import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorkerStatus } from '../../api';
import { LaunchSessionForm, type LaunchSessionFormState } from '../LaunchSessionForm';

const healthyWorkers: WorkerStatus[] = [
  { name: 'alpha', healthy: true, detail: {}, supports_vnc: true },
  { name: 'beta', healthy: true, detail: {}, supports_vnc: false },
];

const baseForm: LaunchSessionFormState = {
  worker: undefined,
  headless: false,
  idle: 300,
  startUrl: '',
  labels: '',
  vnc: false,
  startUrlWait: 'load',
};

afterEach(() => cleanup());

describe('LaunchSessionForm', () => {
  it('filters workers based on VNC requirement', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <LaunchSessionForm
        form={baseForm}
        healthyWorkers={healthyWorkers}
        loading={false}
        error={null}
        startUrlWaitOptions={['load', 'domcontentloaded', 'none']}
        onChange={onChange}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByRole('option', { name: 'alpha' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'beta' })).toBeInTheDocument();

    rerender(
      <LaunchSessionForm
        form={{ ...baseForm, vnc: true }}
        healthyWorkers={healthyWorkers}
        loading={false}
        error={null}
        startUrlWaitOptions={['load', 'domcontentloaded', 'none']}
        onChange={onChange}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByRole('option', { name: 'alpha' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'beta' })).not.toBeInTheDocument();
    expect(screen.queryByText('Workers with VNC support are unavailable.')).not.toBeInTheDocument();
  });

  it('shows hint when VNC is enabled but no workers support it', () => {
    render(
      <LaunchSessionForm
        form={{ ...baseForm, vnc: true }}
        healthyWorkers={[{ name: 'gamma', healthy: true, detail: {}, supports_vnc: false }]}
        loading={false}
        error={null}
        startUrlWaitOptions={['load']}
        onChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText('Workers with VNC support are unavailable.')).toBeInTheDocument();
  });

  it('calls onChange when toggles are interacted with', () => {
    const onChange = vi.fn();
    render(
      <LaunchSessionForm
        form={baseForm}
        healthyWorkers={healthyWorkers}
        loading={false}
        error={null}
        startUrlWaitOptions={['load']}
        onChange={onChange}
        onSubmit={vi.fn()}
      />,
    );

    const headlessToggle = screen.getByLabelText('Headless');
    expect(headlessToggle).not.toBeDisabled();
    fireEvent.click(headlessToggle);
    expect(onChange).toHaveBeenCalledWith({ headless: true });

    const idleField = screen.getByLabelText('Idle TTL (seconds)');
    fireEvent.change(idleField, { target: { value: '600' } });
    expect(onChange).toHaveBeenCalledWith({ idle: 600 });
  });

  it('renders error message and disables submit when loading', () => {
    render(
      <LaunchSessionForm
        form={baseForm}
        healthyWorkers={healthyWorkers}
        loading
        error="Something went wrong"
        startUrlWaitOptions={['load']}
        onChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Launching…' })).toBeDisabled();
  });
});
