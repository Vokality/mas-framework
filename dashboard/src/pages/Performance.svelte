<script lang="ts">
  import type { ManagementSnapshot, PerformancePoint } from '../lib/contracts';
  import { exportJson, milliseconds, number, percent, timestamp } from '../lib/format';
  import PageHeader from '../components/PageHeader.svelte';
  import Button from '../components/Button.svelte';
  import Panel from '../components/Panel.svelte';
  import TrendChart from '../components/TrendChart.svelte';
  import Table from '../components/Table.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Pagination from '../components/Pagination.svelte';
  import Notice from '../components/Notice.svelte';
  let {
    snapshot,
    history,
    error,
    limit = $bindable(120),
  }: {
    snapshot: ManagementSnapshot;
    history: PerformancePoint[] | null;
    error: string;
    limit?: number;
  } = $props();
  let windowMinutes = $state(5),
    page = $state(1),
    onlyComplete = $state(false);
  let points = $derived(
    (history ?? []).filter(
      (point) =>
        !windowMinutes ||
        point.finished_at >= (history?.at(-1)?.finished_at ?? 0) - windowMinutes * 60,
    ),
  );
  let rows = $derived(points.filter((point) => !onlyComplete || point.complete).toReversed());
  let current = $derived(Math.min(page, Math.max(1, Math.ceil(rows.length / 20))));
  let fleet = $derived(snapshot.fleet);
  $effect(() => {
    windowMinutes;
    onlyComplete;
    limit;
    page = 1;
  });
</script>

<PageHeader
  title="Performance"
  description="Retained throughput and delivery latency, with measured coverage and explicit targets."
>
  {#snippet actions()}<Button
      disabled={!points.length}
      onclick={() => exportJson('performance', points)}>↓ Export window</Button
    >{/snippet}
</PageHeader>
{#if error}<Notice
    title="History unavailable"
    message={`${error}${history ? ' Displayed history is stale.' : ''}`}
    kind="error"
  />{/if}
<div class="filter-bar">
  <label
    ><span class="field-label">Time window</span><select bind:value={windowMinutes}
      ><option value={1}>Last minute</option><option value={5}>Last 5 minutes</option><option
        value={0}>All fetched intervals</option
      ></select
    ></label
  >
  <label
    ><span class="field-label">Retained intervals to fetch</span><select bind:value={limit}
      ><option value={120}>120 intervals</option><option value={300}>300 intervals</option><option
        value={500}>500 intervals</option
      ></select
    ></label
  >
  <label style="display:flex;align-items:center;gap:8px;padding:8px"
    ><input type="checkbox" bind:checked={onlyComplete} /> Complete table rows only</label
  >
</div>
<p class="scope-note">
  Window ends at the newest fetched interval. Up to {limit} retained intervals are available in this view;
  shorter retention or gaps can reduce the window.
</p>
<div class="panels">
  <Panel title="Accepted throughput"
    ><TrendChart
      {points}
      field="accepted_rate"
      target={fleet?.targets.accepted_rate ?? null}
      label="Messages admitted"
      unit="msg/sec"
    /></Panel
  >
  <Panel title="Send → delivery latency"
    ><TrendChart
      {points}
      field="end_to_end_p95_ms"
      target={fleet?.targets.end_to_end_p95_ms ?? null}
      label="Correlated p95"
      unit="ms"
    /></Panel
  >
</div>
<p class="scope-note">
  {fleet?.performance.clock_scope ?? 'No fleet clock scope available.'} Missing measurements appear as
  gaps.
</p>
<div class="section-label">
  <h2>Measured intervals</h2>
  <p>{points.length} in selected window</p>
</div>
<Table
  caption="Retained performance intervals"
  headings={[
    'Finished',
    'Accepted / sec',
    'p95 delivery',
    'Joined coverage',
    'Counters',
    'Timing',
    'Redis errors',
  ]}
  empty={!rows.length}
  emptyMessage={history === null
    ? 'Waiting for retained history…'
    : 'No measurements match this window.'}
>
  {#each rows.slice((current - 1) * 20, current * 20) as point}<tr
      ><td>{timestamp(point.finished_at)}</td><td>{number(point.accepted_rate)}</td><td
        >{milliseconds(point.end_to_end_p95_ms)}</td
      ><td
        >{percent(point.latency_coverage)}
        <div class="small">{number(point.latency_samples)} paired messages</div></td
      ><td><StatusBadge status={point.counter_complete ? 'complete' : 'partial'} /></td><td
        ><StatusBadge status={point.complete ? 'complete' : 'partial'} /></td
      ><td>{number(point.redis_errors)}</td></tr
    >{/each}
</Table>
<Pagination bind:page total={rows.length} />
