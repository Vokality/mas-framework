<script lang="ts">
  import type { ManagementSnapshot, TraceSummary } from '../lib/contracts';
  import { exportJson, milliseconds, number, timestamp } from '../lib/format';
  import PageHeader from '../components/PageHeader.svelte';
  import Button from '../components/Button.svelte';
  import FilterBar from '../components/FilterBar.svelte';
  import Table from '../components/Table.svelte';
  import Pagination from '../components/Pagination.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Notice from '../components/Notice.svelte';
  let {
    snapshot,
    traces,
    error,
    limit = $bindable(100),
  }: {
    snapshot: ManagementSnapshot;
    traces: TraceSummary[] | null;
    error: string;
    limit?: number;
  } = $props();
  let search = $state(''),
    selectedState = $state(''),
    sort = $state('newest'),
    page = $state(1);
  let filtered = $derived(
    (traces ?? [])
      .filter(
        (trace) =>
          `${trace.trace_id} ${trace.message_ids.join(' ')} ${trace.services.join(' ')}`
            .toLowerCase()
            .includes(search.toLowerCase()) &&
          (!selectedState ||
            (selectedState === 'errors'
              ? trace.error_count > 0
              : selectedState === 'complete'
                ? trace.complete
                : !trace.complete)),
      )
      .toSorted((a, b) =>
        sort === 'slowest'
          ? (b.end_to_end_ms ?? -1) - (a.end_to_end_ms ?? -1)
          : b.started_at - a.started_at,
      ),
  );
  let current = $derived(Math.min(page, Math.max(1, Math.ceil(filtered.length / 20))));
  $effect(() => {
    search;
    selectedState;
    sort;
    page = 1;
  });
</script>

<PageHeader
  title="Message traces"
  description="Inspect sampled message journeys, stage timings, and correlation metadata."
  >{#snippet actions()}<Button
      disabled={!filtered.length}
      onclick={() => exportJson('traces', filtered)}>↓ Export selection</Button
    >{/snippet}</PageHeader
>
{#if error}<Notice
    title="Trace list unavailable"
    message={`${error}${traces ? ' Displayed list is stale.' : ''}`}
    kind="error"
  />{/if}
<FilterBar
  bind:search
  bind:state={selectedState}
  bind:sort
  placeholder="Trace, message ID, or service"
  stateLabel="Coverage"
  stateOptions={[
    { value: '', label: 'All traces' },
    { value: 'errors', label: 'Span errors' },
    { value: 'complete', label: 'Complete' },
    { value: 'partial', label: 'Partial' },
  ]}
  sortOptions={[
    { value: 'newest', label: 'Newest first' },
    { value: 'slowest', label: 'Slowest first' },
  ]}
>
  <label
    ><span class="field-label">Fetch limit</span><select bind:value={limit}
      ><option value={100}>100 traces</option><option value={300}>300 traces</option><option
        value={500}>500 traces</option
      ></select
    ></label
  >
</FilterBar>
<Table
  caption="Retained message traces"
  headings={[
    'Trace / message',
    'Started',
    'Services',
    'Spans',
    'Send → delivery',
    'Errors',
    'Coverage',
  ]}
  empty={!filtered.length}
  emptyMessage={traces === null
    ? 'Waiting for retained traces…'
    : 'No retained traces match these filters.'}
>
  {#each filtered.slice((current - 1) * 20, current * 20) as trace}<tr
      ><td
        ><a href={`/traces/${trace.trace_id}`}>{trace.trace_id.slice(0, 16)}…</a>
        <div class="small">{trace.message_ids[0] ?? 'No message identity'}</div></td
      ><td>{timestamp(trace.started_at)}</td><td class="wrap" style="max-width:220px"
        >{trace.services.join(' · ')}</td
      ><td>{number(trace.span_count)}</td><td>{milliseconds(trace.end_to_end_ms)}</td><td
        ><StatusBadge
          status={trace.error_count ? 'failed' : 'healthy'}
          label={number(trace.error_count)}
        /></td
      ><td
        ><StatusBadge
          status={trace.clock_skew_detected ? 'warning' : trace.complete ? 'complete' : 'partial'}
          label={trace.clock_skew_detected ? 'Clock skew' : trace.complete ? 'Complete' : 'Partial'}
        /></td
      ></tr
    >{/each}
</Table>
<Pagination bind:page total={filtered.length} />
<p class="scope-note">
  {snapshot.fleet
    ? `Details retain a deterministic 1-in-${snapshot.fleet.trace_sample_every} sample plus failed traces, up to ${number(snapshot.fleet.trace_limit)} traces and ${number(snapshot.fleet.retention_seconds / 60)} minutes. Full configured OTLP export is independent of this retained sample.`
    : 'Fleet capture and retention configuration unavailable.'}
</p>
