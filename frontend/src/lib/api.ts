// Thin API layer. One typed function per backend endpoint.

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface Health {
  status: string;
  app_name: string;
  environment: string;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

// --- /api/ask (Server-Sent Events) ---

// The frame shapes the backend emits (see backend/app/routers/ask.py).
export type AskEvent =
  | { type: "sql"; sql: string; rationale: string }
  | { type: "rows"; columns: string[]; rows: unknown[][] }
  | { type: "delta"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

// Stream the ask pipeline. `onEvent` fires for each SSE frame as it arrives.
// EventSource only supports GET, so we POST and parse the `data: {json}\n\n`
// frames off the response body reader ourselves.
export async function askStream(
  question: string,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`API /ask failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. Keep the trailing partial in buffer.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const json = line.slice("data:".length).trim();
      if (json) onEvent(JSON.parse(json) as AskEvent);
    }
  }
}
