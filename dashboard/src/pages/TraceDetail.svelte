<script lang="ts">
  import type { TraceDetail } from '../lib/contracts';
  import { exportJson, milliseconds, number, timestamp } from '../lib/format';
  import PageHeader from '../components/PageHeader.svelte';
  import Button from '../components/Button.svelte';
  import MetricCard from '../components/MetricCard.svelte';
  import Panel from '../components/Panel.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Notice from '../components/Notice.svelte';
  let { traceId, detail, error }: { traceId: string; detail: TraceDetail | null; error: string } =
    $props();
  let search = $state(''),
    errorsOnly = $state(false);
  let spans = $derived(
    detail?.spans.filter(
      (entry) =>
        `${entry.span.name} ${entry.span.service_name}`
          .toLowerCase()
          .includes(search.toLowerCase()) &&
        (!errorsOnly || entry.span.failed),
    ) ?? [],
  );
  let totalMs = $derived(
    detail ? Math.max(0.001, (detail.summary.finished_at - detail.summary.started_at) * 1000) : 1,
  );
</script>

<PageHeader title="Trace detail" description={traceId}
  >{#snippet actions()}<a class="button" href="/traces">← All traces</a><Button
      disabled={!detail}
      onclick={() => exportJson(`trace-${traceId}`, detail)}>↓ Export trace</Button
    >{/snippet}</PageHeader
>
{#if error}<Notice title="Trace unavailable" message={error} kind="error" />{/if}
{#if detail}
  <div class="metrics">
    <MetricCard
      label="Send → delivery"
      value={milliseconds(detail.summary.end_to_end_ms)}
      hint="Paired delivery timing"
    /><MetricCard
      label="Spans"
      value={number(detail.summary.span_count)}
      hint={`${detail.summary.services.length} observed services`}
    /><MetricCard label="Span errors" value={number(detail.summary.error_count)} /><MetricCard
      label="Coverage"
      value={detail.summary.clock_skew_detected
        ? 'Clock skew'
        : detail.summary.complete
          ? 'Complete'
          : 'Partial'}
    />
  </div>
  <Panel title="Message & correlation"
    ><div class="metric-row">
      <span>Started</span><strong>{timestamp(detail.summary.started_at)}</strong>
    </div>
    <div class="metric-row">
      <span>Finished</span><strong>{timestamp(detail.summary.finished_at)}</strong>
    </div>
    <p class="scope-note wrap">
      Messages: {detail.summary.message_ids.join(' · ') || 'No message identifiers retained.'}
    </p>
    <p class="scope-note">{detail.clock_scope}</p></Panel
  >
  <div class="filter-bar">
    <label class="search-field"
      ><span class="field-label">Find a stage</span><input
        type="search"
        bind:value={search}
        placeholder="Operation or service"
      /></label
    ><label style="display:flex;gap:8px;align-items:center;padding:8px"
      ><input type="checkbox" bind:checked={errorsOnly} /> Span errors only</label
    >
  </div>
  <Panel
    title="Stage waterfall"
    description={`${spans.length} visible / ${detail.spans.length} retained spans`}
  >
    {#each spans as entry}<div class="waterfall-row">
        <div class="span-label" style:padding-left={`${Math.min(entry.depth, 6) * 10}px`}>
          <strong>{entry.span.name}</strong>
          <div class="muted small">{entry.span.service_name}</div>
          <details>
            <summary>Span metadata</summary>
            <div class="metadata">
              Span: {entry.span.span_id}<br />Parent: {entry.span.parent_span_id ?? 'Root'}<br
              />Trace: {entry.span
                .trace_id}{#each Object.entries(entry.span.attributes) as [name, value]}<div>
                  {name}: {String(value)}
                </div>{/each}
            </div>
          </details>
        </div>
        <div class="waterfall-track">
          <div
            class="waterfall-bar"
            class:failed={entry.span.failed}
            style:left={`${Math.max(0, (entry.offset_ms / totalMs) * 100)}%`}
            style:width={`${Math.min(100, (entry.duration_ms / totalMs) * 100)}%`}
            title={`${milliseconds(entry.offset_ms)} offset; ${milliseconds(entry.duration_ms)} duration`}
          ></div>
        </div>
        <div class="waterfall-time">
          {milliseconds(entry.duration_ms)}
          <div style="margin-top:6px">
            <StatusBadge
              status={entry.span.failed ? 'failed' : 'healthy'}
              label={entry.span.failed ? 'Error' : 'OK'}
            />
          </div>
        </div>
      </div>{/each}
    {#if !spans.length}<div class="empty">No stages match these filters.</div>{/if}
  </Panel>
{:else if !error}<div class="loading" role="status">Loading retained trace…</div>{/if}
