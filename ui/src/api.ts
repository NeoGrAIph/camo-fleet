export interface WorkerStatus {
  name: string;
  healthy: boolean;
  detail: Record<string, unknown>;
}

export interface SessionItem {
  worker: string;
  id: string;
  status: string;
  created_at: string;
  last_seen_at: string;
  browser: string;
  headless: boolean;
  idle_ttl_seconds: number;
  labels: Record<string, string>;
  ws_endpoint: string;
  vnc: {
    ws?: string | null;
    http?: string | null;
    password_protected?: boolean;
  };
}

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchWorkers(): Promise<WorkerStatus[]> {
  return request<WorkerStatus[]>('/workers');
}

export function fetchSessions(): Promise<SessionItem[]> {
  return request<SessionItem[]>('/sessions');
}

export function createSession(payload: {
  worker?: string;
  browser?: string;
  headless?: boolean;
  idle_ttl_seconds?: number;
}): Promise<SessionItem> {
  return request<SessionItem>('/sessions', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
