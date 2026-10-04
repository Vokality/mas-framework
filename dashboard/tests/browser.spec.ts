import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import type {
  ManagementSnapshot,
  PerformancePoint,
  TraceDetail,
  TraceSummary,
} from '../src/lib/contracts';
import { isHistory, isSnapshot, isTrace, isTraces } from '../src/lib/validators.js';

interface BrowserExamples {
  snapshot: ManagementSnapshot;
  empty_snapshot: ManagementSnapshot;
  history: PerformancePoint[];
  traces: TraceSummary[];
  trace: TraceDetail;
}

function readExamples(): BrowserExamples {
  const value: unknown = JSON.parse(
    readFileSync(new URL('./browser-fixtures.json', import.meta.url), 'utf8'),
  );
  if (
    typeof value !== 'object' ||
    value === null ||
    !('snapshot' in value) ||
    !('empty_snapshot' in value) ||
    !('history' in value) ||
    !('traces' in value) ||
    !('trace' in value) ||
    !isSnapshot(value.snapshot) ||
    !isSnapshot(value.empty_snapshot) ||
    !isHistory(value.history) ||
    !isTraces(value.traces) ||
    !isTrace(value.trace)
  ) {
    throw new Error('Browser fixtures do not match the Python serialization contracts');
  }
  return {
    snapshot: value.snapshot,
    empty_snapshot: value.empty_snapshot,
    history: value.history,
    traces: value.traces,
    trace: value.trace,
  };
}

const examples = readExamples();
const hostile = '<img src=x onerror=globalThis.__xss=1>';

interface ApiState {
  snapshot: ManagementSnapshot;
  snapshotMode: 'ok' | 'unavailable' | 'invalid';
  history: PerformancePoint[];
  traces: TraceSummary[];
  historyStatus: number;
  tracesStatus: number;
  requireToken: boolean;
  paths: string[];
  authorizations: (string | undefined)[];
}

async function api(page: Page, empty = false): Promise<ApiState> {
  const state: ApiState = {
    snapshot: empty ? examples.empty_snapshot : examples.snapshot,
    snapshotMode: 'ok',
    history: empty ? [] : examples.history,
    traces: empty ? [] : examples.traces,
    historyStatus: 200,
    tracesStatus: 200,
    requireToken: false,
    paths: [],
    authorizations: [],
  };
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    state.paths.push(url.pathname + url.search);
    const authorization = request.headers().authorization;
    state.authorizations.push(authorization);
    if (state.requireToken && authorization !== 'Bearer memory-only-reader') {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: '{"error":"reader_required"}',
      });
      return;
    }
    const limit = Number(url.searchParams.get('limit') ?? 500);
    if (url.pathname === '/api/snapshot') {
      await route.fulfill({
        status: state.snapshotMode === 'unavailable' ? 503 : 200,
        contentType: 'application/json',
        body: JSON.stringify(
          state.snapshotMode === 'ok'
            ? state.snapshot
            : state.snapshotMode === 'invalid'
              ? { health: { status: 'healthy' } }
              : { error: 'backend unavailable private detail' },
        ),
      });
    } else if (url.pathname === '/api/history') {
      await route.fulfill({
        status: state.historyStatus,
        contentType: 'application/json',
        body: JSON.stringify(state.history.slice(-limit)),
      });
    } else if (url.pathname === '/api/traces') {
      await route.fulfill({
        status: state.tracesStatus,
        contentType: 'application/json',
        body: JSON.stringify(state.traces.slice(-limit)),
      });
    } else if (url.pathname === `/api/traces/${examples.trace.summary.trace_id}`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(examples.trace),
      });
    } else {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: '{"error":"not_found"}',
      });
    }
  });
  return state;
}

async function pause(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Ⅱ Pause', exact: true }).click();
  await expect(page.getByText('Auto-refresh paused', { exact: false })).toBeVisible();
}

const routes = [
  ['overview', 'System overview'],
  ['fleet', 'Fleet'],
  ['performance', 'Performance'],
  ['traces', 'Message traces'],
  ['alerts', 'Operational alerts'],
  ['agents', 'Agents'],
  ['queues', 'Queues'],
  ['activity', 'Activity'],
  ['telemetry', 'Telemetry'],
] as const;

test('all pages support real links, deep links and browser history', async ({ page }) => {
  await api(page);
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
  await pause(page);
  for (const [path, heading] of routes.slice(1)) {
    await page.locator(`nav a[href="/${path}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/${path}$`));
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    await expect(page.locator(`nav a[href="/${path}"]`)).toHaveAttribute('aria-current', 'page');
  }
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Activity', exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading', { name: 'Telemetry', exact: true })).toBeVisible();
  await page.goto('/performance');
  await expect(page.getByRole('heading', { name: 'Performance', exact: true })).toBeVisible();
});

test('trace filters, sorting, pagination and waterfall controls work', async ({ page }) => {
  const state = await api(page);
  await page.goto('/traces');
  const table = page.getByRole('region', { name: 'Retained message traces' });
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await pause(page);
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('21–40 of 45', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '← Previous', exact: true }).click();
  await expect(page.getByText('1–20 of 45', { exact: true })).toBeVisible();
  await page.getByLabel('Search', { exact: true }).fill('message-03');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table).toContainText('message-03');
  await page.getByLabel('Search', { exact: true }).fill('');
  await page.getByLabel('Coverage').selectOption('errors');
  await expect(table.locator('tbody tr')).toHaveCount(15);
  await page.getByLabel('Coverage').selectOption('');
  await page.getByLabel('Sort').selectOption('slowest');
  await expect(table.locator('tbody tr').first()).toContainText('message-45');
  await page.getByLabel('Fetch limit').selectOption('300');
  await expect.poll(() => state.paths.includes('/api/traces?limit=300')).toBe(true);
  await table.locator(`a[href="/traces/${examples.trace.summary.trace_id}"]`).click();
  await expect(page.getByRole('heading', { name: 'Trace detail', exact: true })).toBeVisible();
  await expect(page.locator('.waterfall-row')).toHaveCount(3);
  await expect(page.locator('.waterfall-bar').first()).toHaveAttribute('style', /width/);
  await page.getByLabel('Find a stage', { exact: true }).fill('mas.rpc.send');
  await expect(page.locator('.waterfall-row')).toHaveCount(1);
  await page.getByLabel('Find a stage', { exact: true }).fill('');
  await page.getByLabel('Span errors only', { exact: true }).check();
  await expect(page.locator('.waterfall-row')).toHaveCount(1);
  await expect(page.locator('.waterfall-row')).toContainText('mas.agent.handle_message');
  await page.getByText('Span metadata', { exact: true }).click();
  await expect(page.locator('.metadata')).toContainText(hostile);
  await page.getByRole('link', { name: '← All traces', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Message traces', exact: true })).toBeVisible();
});

test('performance window, completeness, fetch limit, pagination and export work', async ({
  page,
}) => {
  const state = await api(page);
  await page.goto('/performance');
  const table = page.getByRole('region', { name: 'Retained performance intervals' });
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await pause(page);
  await page.getByLabel('Retained intervals to fetch').selectOption('500');
  await expect.poll(() => state.paths.includes('/api/history?limit=500')).toBe(true);
  await expect(page.getByText('180 in selected window', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('21–40 of 180', { exact: true })).toBeVisible();
  await page.getByLabel('Time window').selectOption('1');
  await expect(page.getByText('61 in selected window', { exact: true })).toBeVisible();
  await page.getByLabel('Complete table rows only', { exact: true }).check();
  await expect(page.getByText('1–20 of 30', { exact: true })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '↓ Export window', exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/performance.*\.json$/);
});

test('refresh cadence, pause, resume and manual refresh control actual requests', async ({
  page,
}) => {
  await page.clock.install();
  const state = await api(page);
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
  await page.getByLabel('Refresh cadence', { exact: true }).selectOption('2');
  const initial = state.paths.length;
  await page.clock.runFor(2100);
  await expect.poll(() => state.paths.length).toBeGreaterThan(initial);
  await pause(page);
  const paused = state.paths.length;
  await page.clock.runFor(10_000);
  expect(state.paths.length).toBe(paused);
  await page.getByRole('button', { name: '↻ Refresh', exact: true }).click();
  await expect.poll(() => state.paths.length).toBeGreaterThan(paused);
  await page.getByRole('button', { name: '▶ Resume', exact: true }).click();
  const resumed = state.paths.length;
  await page.clock.runFor(2100);
  await expect.poll(() => state.paths.length).toBeGreaterThan(resumed);
});

test('401 access flow sends memory-only tokens and clears them on reload', async ({ page }) => {
  const state = await api(page);
  state.requireToken = true;
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: 'Management access', exact: true })).toBeVisible();
  await page.getByLabel('Reader access token', { exact: true }).fill('memory-only-reader');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
  expect(state.authorizations).toContain('Bearer memory-only-reader');
  expect(
    await page.evaluate(() =>
      JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }),
    ),
  ).not.toContain('memory-only-reader');
  await expect(page).toHaveURL(/\/overview$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Management access', exact: true })).toBeVisible();
  expect(state.authorizations.at(-1)).toBeUndefined();
  await expect(page.getByLabel('Reader access token', { exact: true })).toHaveValue('');
});

for (const mode of ['unavailable', 'invalid'] as const) {
  test(`failed ${mode} refresh keeps the verified snapshot with explicit stale status`, async ({
    page,
  }) => {
    const state = await api(page);
    await page.goto('/overview');
    await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
    await pause(page);
    state.snapshotMode = mode;
    await page.getByRole('button', { name: '↻ Refresh', exact: true }).click();
    await expect(page.getByText('Displayed snapshot is stale', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
    await expect(page.getByText('backend unavailable private detail')).toHaveCount(0);
    state.snapshotMode = 'ok';
    await page.getByRole('button', { name: '↻ Refresh', exact: true }).click();
    await expect(page.getByText('Displayed snapshot is stale', { exact: true })).toHaveCount(0);
  });
}

test('history failure preserves prior intervals and trace 404 is explicit', async ({ page }) => {
  const state = await api(page);
  await page.goto('/performance');
  const table = page.getByRole('region', { name: 'Retained performance intervals' });
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await pause(page);
  state.historyStatus = 503;
  await page.getByRole('button', { name: '↻ Refresh', exact: true }).click();
  await expect(page.getByText('History unavailable', { exact: true })).toBeVisible();
  await expect(page.getByText('Displayed history is stale.', { exact: false })).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await page.goto(`/traces/${'f'.repeat(32)}`);
  await expect(page.getByText('Trace unavailable', { exact: true })).toBeVisible();
  await expect(
    page.getByText('The retained trace is no longer available.', { exact: true }),
  ).toBeVisible();
});

test('hostile remote text remains inert in lists and span metadata', async ({ page }) => {
  await api(page);
  await page.goto('/activity');
  await expect(
    page.getByRole('region', { name: 'Recent authorization decision metadata' }),
  ).toContainText(hostile);
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  await page.goto(`/traces/${examples.trace.summary.trace_id}`);
  await expect(page.locator('.waterfall-row')).toHaveCount(3);
  await page.getByText('Span metadata', { exact: true }).first().click();
  await expect(page.locator('.metadata').first()).toContainText(hostile);
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(globalThis, '__xss'))).toBeUndefined();
});

test('empty data and unknown routes are understandable', async ({ page }) => {
  await api(page, true);
  for (const path of ['agents', 'queues', 'activity', 'traces', 'performance', 'alerts']) {
    await page.goto(`/${path}`);
    if (path === 'agents')
      await expect(page.getByText('No matching agents', { exact: true })).toBeVisible();
    else await expect(page.locator('.empty').first()).toBeVisible();
    await expect(page.locator('tbody tr img')).toHaveCount(0);
  }
  const unknown = await page.goto('/unknown-page');
  expect(unknown?.status()).toBe(404);
  await expect(page.getByRole('navigation', { name: 'Management pages' })).toHaveCount(0);
});

test('390px pages and waterfall remain within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await api(page);
  for (const [path, heading] of [
    ...routes,
    [`traces/${examples.trace.summary.trace_id}`, 'Trace detail'],
  ] as const) {
    await page.goto(`/${path}`);
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
    await expect(page.getByRole('navigation', { name: 'Management pages' })).toBeVisible();
  }
});

test('fleet search, status, sort, pagination, reset and details work', async ({ page }) => {
  await api(page);
  await page.goto('/fleet');
  const table = page.getByRole('region', { name: 'Fleet member health and heartbeat freshness' });
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await pause(page);
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('21–30 of 30', { exact: true })).toBeVisible();
  await page.getByLabel('Find a broker', { exact: true }).fill('broker-03');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table).toContainText('broker-03');
  await page.getByRole('button', { name: 'Reset filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Health', exact: true }).selectOption('stale');
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await page.getByRole('combobox', { name: 'Health', exact: true }).selectOption('');
  await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('health');
  await expect(table.locator('tbody tr').first()).toContainText('broker-03');
  await page.getByText('Sessions (1)', { exact: true }).first().click();
  await expect(page.locator('.agent-card').first()).toContainText('worker-1');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export fleet', exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/fleet.*\.json$/);
});

test('agent filters, pagination, sort, reset and instance details work', async ({ page }) => {
  await api(page);
  await page.goto('/agents');
  await expect(page.locator('.agent-card')).toHaveCount(20);
  await pause(page);
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('21–30 of 30', { exact: true })).toBeVisible();
  await page.getByLabel('Find an agent', { exact: true }).fill('agent-01');
  await expect(page.locator('.agent-card')).toHaveCount(1);
  await page.getByText('Connected instances (1)', { exact: true }).click();
  await expect(page.locator('.metadata')).toContainText('worker-1');
  await page.getByRole('button', { name: 'Reset filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Availability', exact: true }).selectOption('inactive');
  await expect(page.locator('.agent-card')).toHaveCount(10);
  await page.getByRole('combobox', { name: 'Availability', exact: true }).selectOption('');
  await page.getByRole('combobox', { name: 'Capability', exact: true }).selectOption(hostile);
  await expect(page.locator('.agent-card')).toHaveCount(1);
  await expect(page.locator('.agent-card')).toContainText('agent-01');
  await page.getByRole('button', { name: 'Reset filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('sessions');
  await expect(page.locator('.agent-card').first()).toContainText('agent-01');
});

test('queue search, work state, sort, reset and pagination work', async ({ page }) => {
  await api(page);
  await page.goto('/queues');
  const table = page.getByRole('region', {
    name: 'Shared stream backlog and pending acknowledgements',
  });
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await expect(table.locator('tbody tr').first()).toContainText('agent.stream:agent-30');
  await pause(page);
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('21–30 of 30', { exact: true })).toBeVisible();
  await page.getByLabel('Find a stream', { exact: true }).fill('agent-30');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'Reset filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Work state', exact: true }).selectOption('waiting');
  await expect(page.getByText('1–20 of 29', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'Work state', exact: true }).selectOption('');
  await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('stream');
  await expect(table.locator('tbody tr').first()).toContainText('agent.stream:agent-01');
  await expect(
    page.getByRole('region', { name: 'Circuit breaker state and admission metadata' }),
  ).toContainText('Allowed');
});

test('activity search, decision, sort, reset, pagination and details work', async ({ page }) => {
  await api(page);
  await page.goto('/activity');
  const table = page.getByRole('region', { name: 'Recent authorization decision metadata' });
  await expect(table.locator('tbody tr')).toHaveCount(20);
  await pause(page);
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('21–30 of 30', { exact: true })).toBeVisible();
  await page.getByLabel('Find an event', { exact: true }).fill('activity-03');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await page.getByText('Decision detail', { exact: true }).click();
  await expect(page.locator('.metadata')).toContainText('Correlation: None');
  await page.getByRole('button', { name: 'Reset filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Decision', exact: true }).selectOption('AUTHZ_DENIED');
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await page.getByRole('combobox', { name: 'Decision', exact: true }).selectOption('');
  await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('latency');
  await expect(table.locator('tbody tr').first()).toContainText('activity-30');
});

test('alert state, severity, search, sort and pagination work', async ({ page }) => {
  await api(page);
  await page.goto('/alerts');
  await expect(page.locator('.alert-card')).toHaveCount(12);
  await pause(page);
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByText('13–15 of 15', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'State', exact: true }).selectOption('resolved');
  await expect(page.getByText('1–12 of 15', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'Severity', exact: true }).selectOption('critical');
  await expect(page.locator('.alert-card')).toHaveCount(5);
  await page.getByRole('combobox', { name: 'Severity', exact: true }).selectOption('');
  await page.getByLabel('Search', { exact: true }).fill('Condition 01');
  await expect(page.locator('.alert-card')).toHaveCount(1);
  await page.getByLabel('Search', { exact: true }).fill('');
  await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('newest');
  await expect(page.locator('.alert-card').first()).toContainText('Condition 01');
});

test('actual exporter state and signal filters work', async ({ page }) => {
  await api(page);
  await page.goto('/telemetry');
  const table = page.getByRole('region', { name: 'Telemetry exporter outcomes' });
  await expect(table.locator('tbody tr')).toHaveCount(60);
  await pause(page);
  await page.getByRole('combobox', { name: 'Signal', exact: true }).selectOption('metrics');
  await expect(table.locator('tbody tr')).toHaveCount(30);
  await page.getByRole('combobox', { name: 'State', exact: true }).selectOption('degraded');
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await page.getByLabel('Search', { exact: true }).fill('broker-03');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table).toContainText('export_rejected');
  await page.getByLabel('Search', { exact: true }).fill('');
  await page.getByRole('combobox', { name: 'State', exact: true }).selectOption('');
  await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('failures');
  await expect(table.locator('tbody tr').first()).toContainText('broker-03');
});

test('local access and theme controls work without storing reader credentials', async ({
  page,
}) => {
  const state = await api(page);
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '◐ Ink', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'ink');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'ink');
  await page.getByRole('button', { name: 'Access', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Management access', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Use local access', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
  expect(state.authorizations.at(-1)).toBeUndefined();
});

test('bundled assets and page fallbacks retain production CSP and MIME contracts', async ({
  page,
}) => {
  await api(page);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const response = await page.goto('/performance');
  expect(response?.headers()['content-security-policy']).toContain("script-src 'self';");
  expect(response?.headers()['content-security-policy']).toContain("style-src 'self';");
  expect(response?.headers()['content-security-policy']).toContain(
    "style-src-attr 'unsafe-inline';",
  );
  await expect(
    page.getByRole('region', { name: 'Retained performance intervals' }).locator('tbody tr'),
  ).toHaveCount(20);
  await expect(page.locator('.trend-chart svg')).toHaveCount(2);
  expect(errors).toEqual([]);
  const script = await page.request.get('/assets/dashboard.js');
  const style = await page.request.get('/assets/dashboard.css');
  expect(script.headers()['content-type']).toContain('text/javascript');
  expect(style.headers()['content-type']).toContain('text/css');
  expect(script.headers()['x-content-type-options']).toBe('nosniff');
  expect((await page.request.get('/assets/missing.js')).status()).toBe(404);
});

for (const [path, button, filename, count] of [
  ['traces', '↓ Export selection', 'traces', 45],
  ['alerts', '↓ Export selection', 'alerts', 15],
  ['agents', 'Export agents', 'agents', 30],
  ['activity', 'Export activity', 'activity', 30],
  ['telemetry', '↓ Export selection', 'exporters', 60],
] as const) {
  test(`${path} exports the displayed selection as valid JSON`, async ({ page }, testInfo) => {
    await api(page);
    await page.goto(`/${path}`);
    const action = page.getByRole('button', { name: button, exact: true });
    await expect(action).toBeEnabled();
    const downloading = page.waitForEvent('download');
    await action.click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe(`mas-${filename}.json`);
    const destination = testInfo.outputPath(`${filename}.json`);
    await download.saveAs(destination);
    const exported: unknown = JSON.parse(readFileSync(destination, 'utf8'));
    expect(Array.isArray(exported)).toBe(true);
    if (!Array.isArray(exported)) throw new Error('Expected the displayed JSON selection');
    expect(exported.length).toBe(count);
  });
}

test('unavailable fleet keeps alert counts and configured SLO targets unknown', async ({
  page,
}) => {
  const state = await api(page);
  state.snapshot = { ...examples.snapshot, fleet: null };
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: 'System overview', exact: true })).toBeVisible();
  await expect(
    page.locator('.metric-card').filter({ hasText: 'Active conditions' }).locator('.metric-value'),
  ).toHaveText('—');
  await expect(
    page.getByText('Shared fleet observations unavailable', { exact: true }),
  ).toBeVisible();
  await page.goto('/performance');
  await expect(
    page.getByRole('region', { name: 'Retained performance intervals' }).locator('tbody tr'),
  ).toHaveCount(20);
  await expect(page.locator('.chart-target')).toHaveCount(0);
  await expect(page.getByText('Target unavailable', { exact: true })).toHaveCount(2);
});

test('trace and queue exports preserve their render-ready response data', async ({
  page,
}, testInfo) => {
  await api(page);
  await page.goto(`/traces/${examples.trace.summary.trace_id}`);
  const traceAction = page.getByRole('button', { name: '↓ Export trace', exact: true });
  await expect(traceAction).toBeEnabled();
  const tracing = page.waitForEvent('download');
  await traceAction.click();
  const trace = await tracing;
  const tracePath = testInfo.outputPath('trace.json');
  await trace.saveAs(tracePath);
  const detail: unknown = JSON.parse(readFileSync(tracePath, 'utf8'));
  expect(isTrace(detail)).toBe(true);
  expect(detail).toEqual(examples.trace);
  await page.goto('/queues');
  await expect(
    page
      .getByRole('region', { name: 'Shared stream backlog and pending acknowledgements' })
      .locator('tbody tr'),
  ).toHaveCount(20);
  const queuing = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export queues', exact: true }).click();
  const queues = await queuing;
  const queuesPath = testInfo.outputPath('queues.json');
  await queues.saveAs(queuesPath);
  const work: unknown = JSON.parse(readFileSync(queuesPath, 'utf8'));
  if (
    typeof work !== 'object' ||
    work === null ||
    !('queues' in work) ||
    !Array.isArray(work.queues) ||
    !('complete' in work)
  ) {
    throw new Error('Queue export must contain the displayed queue selection and coverage');
  }
  expect(work.queues.length).toBe(30);
  expect(work.complete).toBe(true);
});
