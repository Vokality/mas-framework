import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
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
) {
  throw new Error('Invalid contract fixtures');
}
const { snapshot, history, traces, trace } = examples;
assert.ok(isSnapshot(snapshot));
assert.ok(isHistory(history));
assert.ok(isTraces(traces));
assert.ok(isTrace(trace));

test('actual Python DTO serialization passes every endpoint guard', () => {
  assert.equal(snapshot.fleet?.targets.end_to_end_p95_ms, 300);
  assert.equal(snapshot.telemetry.scope_complete, true);
  assert.equal(trace.spans[0]?.depth, 0);
  assert.equal(trace.spans[0]?.offset_ms, 0);
  assert.equal(isHistory([]), true);
  assert.equal(isTraces([]), true);
});

test('serialized default fields are required, including deeply nested defaults', () => {
  const incomplete: Record<string, unknown> = { ...snapshot };
  delete incomplete.scope;
  assert.equal(isSnapshot(incomplete), false);
  const counters: Record<string, unknown> = { ...snapshot.telemetry };
  delete counters.scope_complete;
  assert.equal(isSnapshot({ ...snapshot, telemetry: counters }), false);
});

test('unknown fields, enum changes and malformed nested counters are rejected', () => {
  assert.equal(isSnapshot({ ...snapshot, secret_payload: 'business-data' }), false);
  assert.equal(isSnapshot({ ...snapshot, health: { ...snapshot.health, status: 'green' } }), false);
  assert.equal(
    isSnapshot({ ...snapshot, telemetry: { ...snapshot.telemetry, delivery_acks: 'many' } }),
    false,
  );
  assert.equal(isSnapshot({ ...snapshot, features: { tracing: 'enabled' } }), false);
});

test('nullable missing observations remain explicit and are accepted', () => {
  assert.equal(
    isSnapshot({
      ...snapshot,
      fleet: null,
      queues: null,
      backlog: null,
      recent_activity: null,
      circuits: null,
    }),
    true,
  );
  assert.equal(
    isHistory(
      history.map((point) => ({
        ...point,
        accepted_rate: null,
        end_to_end_p95_ms: null,
        latency_coverage: null,
        complete: false,
      })),
    ),
    true,
  );
});

test('nonfinite values and invalid trace identities are rejected without mutation', () => {
  assert.equal(isSnapshot({ ...snapshot, uptime_seconds: Number.NaN }), false);
  assert.equal(isSnapshot({ ...snapshot, uptime_seconds: Number.POSITIVE_INFINITY }), false);
  const bad = {
    ...trace,
    spans: trace.spans.map((row) => ({ ...row, span: { ...row.span, span_id: 'not-a-span' } })),
  };
  const original = structuredClone(bad);
  assert.equal(isTrace(bad), false);
  assert.deepEqual(bad, original);
});

test('validators contain no browser eval or dynamic-function compilation', async () => {
  const source = await readFile(new URL('../src/lib/validators.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\beval\s*\(|\bnew\s+Function\s*\(/);
});
