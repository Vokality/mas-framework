import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { ApiError, getHistory, getSnapshot, getTrace, getTraces } from '../src/lib/api.ts';
import { isHistory, isSnapshot, isTrace, isTraces } from '../src/lib/validators.js';

const examples: unknown = JSON.parse(
  await readFile(new URL('./fixtures.json', import.meta.url), 'utf8'),
);
if (
  typeof examples !== 'object' ||
  examples === null ||
  !('snapshot' in examples) ||
  !('history' in examples) ||
  !('traces' in examples) ||
  !('trace' in examples)
)
  throw new Error('Invalid API fixtures');
const { snapshot, history, traces, trace } = examples;
assert.ok(isSnapshot(snapshot));
assert.ok(isHistory(history));
assert.ok(isTraces(traces));
assert.ok(isTrace(trace));

test('every reader returns its validated DTO from the expected same-origin URL', async (t) => {
  const paths: string[] = [];
  t.mock.method(
    globalThis,
    'fetch',
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      assert.equal(typeof input, 'string');
      paths.push(String(input));
      assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer memory-only-token');
      assert.equal(init?.cache, 'no-store');
      assert.equal(init?.credentials, 'omit');
      assert.equal(init?.redirect, 'error');
      if (input === '/api/snapshot') return Response.json(snapshot);
      if (input === '/api/history?limit=120') return Response.json(history);
      if (input === '/api/traces?limit=50') return Response.json(traces);
      return Response.json(trace);
    },
  );
  assert.deepEqual(await getSnapshot('memory-only-token'), snapshot);
  assert.deepEqual(await getHistory('memory-only-token', 120), history);
  assert.deepEqual(await getTraces('memory-only-token', 50), traces);
  assert.deepEqual(await getTrace('memory-only-token', trace.summary.trace_id), trace);
  assert.deepEqual(paths, [
    '/api/snapshot',
    '/api/history?limit=120',
    '/api/traces?limit=50',
    `/api/traces/${trace.summary.trace_id}`,
  ]);
});

test('local reader mode sends no authorization header', async (t) => {
  t.mock.method(
    globalThis,
    'fetch',
    async (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      assert.equal(new Headers(init?.headers).has('Authorization'), false);
      return Response.json(snapshot);
    },
  );
  assert.deepEqual(await getSnapshot(''), snapshot);
});

for (const [status, kind] of [
  [401, 'unauthorized'],
  [403, 'forbidden'],
  [404, 'not_found'],
  [503, 'unavailable'],
  [500, 'http'],
]) {
  test(`HTTP ${status} is distinct and never exposes backend details`, async (t) => {
    if (typeof status !== 'number' || typeof kind !== 'string')
      throw new Error('Invalid test case');
    t.mock.method(
      globalThis,
      'fetch',
      async (): Promise<Response> =>
        new Response('private-token redis://secret-host business-payload', { status }),
    );
    await assert.rejects(getSnapshot(''), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.equal(error.kind, kind);
      assert.doesNotMatch(error.message, /private-token|secret-host|business-payload/);
      return true;
    });
  });
}

test('malformed JSON, HTML and structurally invalid JSON never enter page state', async (t) => {
  const responses = [
    new Response('private broken JSON', { headers: { 'content-type': 'application/json' } }),
    new Response('<script>private</script>', { headers: { 'content-type': 'text/html' } }),
    Response.json({ health: { status: 'healthy' } }),
  ];
  t.mock.method(globalThis, 'fetch', async (): Promise<Response> => {
    const response = responses.shift();
    assert.ok(response);
    return response;
  });
  for (let index = 0; index < 3; index += 1) {
    await assert.rejects(
      getSnapshot(''),
      (error) =>
        error instanceof ApiError &&
        error.kind === 'invalid_response' &&
        !error.message.includes('private'),
    );
  }
});

test('network errors are sanitized', async (t) => {
  t.mock.method(globalThis, 'fetch', async (): Promise<Response> => {
    throw new TypeError('private-network-host reader-token');
  });
  await assert.rejects(
    getSnapshot(''),
    (error) =>
      error instanceof ApiError && error.kind === 'network' && !error.message.includes('private'),
  );
});

test('limits, trace IDs and invalid tokens fail before fetch', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async (): Promise<Response> =>
    Response.json(snapshot),
  );
  for (const limit of [0, 501, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
    await assert.rejects(
      getHistory('', limit),
      (error) => error instanceof ApiError && error.kind === 'invalid_request',
    );
    await assert.rejects(
      getTraces('', limit),
      (error) => error instanceof ApiError && error.kind === 'invalid_request',
    );
  }
  await assert.rejects(
    getTrace('', '../private'),
    (error) => error instanceof ApiError && error.kind === 'invalid_request',
  );
  await assert.rejects(
    getSnapshot('token\nheader'),
    (error) => error instanceof ApiError && error.kind === 'invalid_request',
  );
  assert.equal(fetch.mock.callCount(), 0);
});

test('pre-cancelled requests do not fetch or expose arbitrary abort reasons', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async (): Promise<Response> =>
    Response.json(snapshot),
  );
  const controller = new AbortController();
  controller.abort(new Error('private reason'));
  await assert.rejects(
    getSnapshot('', controller.signal),
    (error) =>
      error instanceof DOMException &&
      error.name === 'AbortError' &&
      !error.message.includes('private'),
  );
  assert.equal(fetch.mock.callCount(), 0);
});

test('pending fetch cancellation preserves AbortError rather than becoming an outage', async (t) => {
  const controller = new AbortController();
  let entered: (() => void) | undefined;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  t.mock.method(
    globalThis,
    'fetch',
    (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
      new Promise((_resolve, reject) => {
        const signal = init?.signal;
        assert.ok(signal);
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
        entered?.();
      }),
  );
  const pending = getSnapshot('', controller.signal);
  await started;
  controller.abort();
  await assert.rejects(
    pending,
    (error) => error instanceof DOMException && error.name === 'AbortError',
  );
});

test('timeout is classified independently from caller cancellation', async (t) => {
  const timeout = new AbortController();
  t.mock.method(AbortSignal, 'timeout', (): AbortSignal => timeout.signal);
  t.mock.method(
    globalThis,
    'fetch',
    (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
      new Promise((_resolve, reject) => {
        const signal = init?.signal;
        assert.ok(signal);
        signal.addEventListener('abort', () => reject(new Error('private timeout')), {
          once: true,
        });
        timeout.abort();
      }),
  );
  await assert.rejects(
    getSnapshot(''),
    (error) =>
      error instanceof ApiError && error.kind === 'timeout' && !error.message.includes('private'),
  );
});
