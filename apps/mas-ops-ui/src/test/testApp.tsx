import { render } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";

import { AuthProvider } from "../auth/AuthProvider";
import { opsRoutes } from "../routes/router";

type MockResponseInit = {
  body?: unknown;
  headers?: HeadersInit;
  status?: number;
  text?: string;
};

type RouteResponse = MockResponseInit | (() => MockResponseInit | Promise<MockResponseInit>);

type RouteStub = {
  method: string;
  path: string;
  response: RouteResponse;
};

export class MockEventSource {
  static instances: Array<MockEventSource> = [];

  readonly listeners = new Map<string, Set<(event: Event) => void>>();
  readonly url: string;
  readonly withCredentials: boolean;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;

  constructor(url: string | URL, init?: EventSourceInit) {
    this.url = String(url);
    this.withCredentials = init?.withCredentials ?? false;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const listeners = this.listeners.get(type) ?? new Set<(event: Event) => void>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  close(): void {}

  emit(eventType: string, data: unknown, lastEventId: string): void {
    const event = new MessageEvent<string>(eventType, {
      data: JSON.stringify(data),
      lastEventId,
    });
    this.listeners.get(eventType)?.forEach((listener) => {
      listener(event);
    });
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }
}

export function installMockFetch(routeStubs: Array<RouteStub>) {
  const queues = new Map<string, Array<RouteResponse>>();
  for (const routeStub of routeStubs) {
    const key = `${routeStub.method.toUpperCase()} ${routeStub.path}`;
    const queue = queues.get(key) ?? [];
    queue.push(routeStub.response);
    queues.set(key, queue);
  }

  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://127.0.0.1:8080");
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${url.pathname}${url.search}`;
    const queue = queues.get(key);
    if (!queue || queue.length === 0) {
      throw new Error(`Unexpected fetch: ${key}`);
    }
    const next = queue.shift();
    if (!next) {
      throw new Error(`No queued response available for ${key}`);
    }
    const resolved = typeof next === "function" ? await next() : next;
    if (resolved.text !== undefined) {
      return new Response(resolved.text, {
        headers: resolved.headers,
        status: resolved.status ?? 200,
      });
    }
    return new Response(
      resolved.body === undefined ? null : JSON.stringify(resolved.body),
      {
        headers: {
          "content-type": "application/json",
          ...(resolved.headers ?? {}),
        },
        status: resolved.status ?? 200,
      },
    );
  });

  vi.stubGlobal("fetch", mock);
  Object.assign(window, { fetch: mock });
  return mock;
}

export function installMockEventSource() {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
  Object.assign(window, { EventSource: MockEventSource });
}

export function renderOpsUi(initialEntry: string) {
  const router = createMemoryRouter(opsRoutes, {
    initialEntries: [initialEntry],
  });
  return render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
}
