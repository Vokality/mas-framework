<script lang="ts">
  import type { ManagementSnapshot } from '../lib/contracts';
  import { exportJson, number, timestamp } from '../lib/format';
  import PageHeader from '../components/PageHeader.svelte';
  import Button from '../components/Button.svelte';
  import FilterBar from '../components/FilterBar.svelte';
  import Table from '../components/Table.svelte';
  import Panel from '../components/Panel.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Notice from '../components/Notice.svelte';
  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let search = $state(''),
    selectedState = $state(''),
    sort = $state('broker'),
    signal = $state('');
  let exporters = $derived(
    snapshot.fleet
      ? snapshot.fleet.brokers.flatMap((member) =>
          member.observation.exporters.map((exporter) => ({
            broker: member.observation.broker_id,
            fresh: member.fresh,
            exporter,
          })),
        )
      : snapshot.exporters.map((exporter) => ({
          broker: snapshot.broker_id ?? 'Local process',
          fresh: true,
          exporter,
        })),
  );
  let filtered = $derived(
    exporters
      .filter(
        (row) =>
          row.broker.toLowerCase().includes(search.toLowerCase()) &&
          (!selectedState || row.exporter.status === selectedState) &&
          (!signal || row.exporter.signal === signal),
      )
      .toSorted((a, b) =>
        sort === 'failures'
          ? b.exporter.failures - a.exporter.failures
          : a.broker.localeCompare(b.broker),
      ),
  );
</script>

<PageHeader
  title="Telemetry"
  description="Exporter acknowledgements, failures, and runtime instrumentation configuration."
  >{#snippet actions()}<Button
      disabled={!filtered.length}
      onclick={() => exportJson('exporters', filtered)}>↓ Export selection</Button
    >{/snippet}</PageHeader
>
{#if !snapshot.fleet}<Notice
    title="Local exporter view"
    message="Fleet observations are unavailable. Outcomes below describe this broker process."
    kind="warning"
  />{/if}
<FilterBar
  bind:search
  bind:state={selectedState}
  bind:sort
  placeholder="Broker ID"
  stateOptions={[
    { value: '', label: 'All states' },
    { value: 'healthy', label: 'Healthy' },
    { value: 'degraded', label: 'Degraded' },
    { value: 'stale', label: 'Stale' },
    { value: 'pending', label: 'Pending' },
    { value: 'disabled', label: 'Disabled' },
  ]}
  sortOptions={[
    { value: 'broker', label: 'Broker name' },
    { value: 'failures', label: 'Most failures' },
  ]}
>
  <label
    ><span class="field-label">Signal</span><select bind:value={signal}
      ><option value="">All signals</option><option value="traces">Traces</option><option
        value="metrics">Metrics</option
      ></select
    ></label
  >
</FilterBar>
<Table
  caption="Telemetry exporter outcomes"
  headings={[
    'Broker / process',
    'Signal',
    'State',
    'Attempts',
    'Success / failure',
    'Items exported',
    'Last success',
    'Last error',
  ]}
  empty={!filtered.length}
>
  {#each filtered as row}<tr
      ><td
        >{row.broker}{#if !row.fresh}<div class="small">Stale broker heartbeat</div>{/if}</td
      ><td>{row.exporter.signal}</td><td><StatusBadge status={row.exporter.status} /></td><td
        >{number(row.exporter.attempts)}</td
      ><td>{number(row.exporter.successes)} / {number(row.exporter.failures)}</td><td
        >{number(row.exporter.exported_items)}</td
      ><td>{timestamp(row.exporter.last_success_at)}</td><td>{row.exporter.last_error ?? '—'}</td
      ></tr
    >{/each}
</Table>
<div class="panels">
  <Panel title="Gateway & runtime"
    ><div class="feature-list">
      {#each Object.entries(snapshot.features) as [name, enabled]}<span class="feature"
          >{name.replaceAll('_', ' ')} <strong>{enabled ? 'on' : 'off'}</strong></span
        >{/each}
    </div>
    <p class="scope-note">
      Settings are reported by the runtime; management access is read only.
    </p></Panel
  ><Panel title="Local message health" description="Process lifetime counters"
    ><div class="metric-row">
      <span>ACK / NACK</span><strong
        >{number(snapshot.telemetry.delivery_acks)} / {number(
          snapshot.telemetry.delivery_nacks,
        )}</strong
      >
    </div>
    <div class="metric-row">
      <span>Retryable NACK</span><strong>{number(snapshot.telemetry.retryable_nacks)}</strong>
    </div>
    <div class="metric-row">
      <span>Dead-letter writes / errors</span><strong
        >{number(snapshot.telemetry.dead_letter_writes)} / {number(
          snapshot.telemetry.dead_letter_errors,
        )}</strong
      >
    </div>
    <div class="metric-row">
      <span>Redis errors</span><strong>{number(snapshot.telemetry.redis_errors)}</strong>
    </div>
    <div class="metric-row">
      <span>Counter scope</span><StatusBadge
        status={snapshot.telemetry.scope_complete ? 'complete' : 'partial'}
      />
    </div></Panel
  >
</div>
<p class="scope-note">
  Exporter outcomes reflect actual acknowledgement, not configuration alone. A stale broker
  heartbeat may make its last reported exporter state outdated.
</p>
