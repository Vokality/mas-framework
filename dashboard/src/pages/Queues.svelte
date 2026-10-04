<script lang="ts">
  import Button from '../components/Button.svelte';
  import FilterBar from '../components/FilterBar.svelte';
  import MetricCard from '../components/MetricCard.svelte';
  import Notice from '../components/Notice.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import Pagination from '../components/Pagination.svelte';
  import Panel from '../components/Panel.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Table from '../components/Table.svelte';
  import type { ManagementSnapshot } from '../lib/contracts';
  import { exportJson, number, timestamp } from '../lib/format';

  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let search = $state('');
  let stateFilter = $state('');
  let sort = $state('pending');
  let page = $state(1);
  let queues = $derived.by(() => {
    const needle = search.trim().toLowerCase();
    return (snapshot.queues ?? [])
      .filter(
        (queue) =>
          (!needle || queue.stream.toLowerCase().includes(needle)) &&
          (!stateFilter ||
            (stateFilter === 'nonempty' &&
              (queue.pending > 0 || (queue.waiting !== null && queue.waiting > 0))) ||
            (stateFilter === 'pending' && queue.pending > 0) ||
            (stateFilter === 'waiting' && queue.waiting !== null && queue.waiting > 0) ||
            (stateFilter === 'unknown' && queue.waiting === null)),
      )
      .sort((a, b) =>
        sort === 'stream'
          ? a.stream.localeCompare(b.stream)
          : b.pending - a.pending || a.stream.localeCompare(b.stream),
      );
  });
  let visible = $derived(
    queues.slice(
      (Math.min(page, Math.max(1, Math.ceil(queues.length / 20))) - 1) * 20,
      Math.min(page, Math.max(1, Math.ceil(queues.length / 20))) * 20,
    ),
  );
  let circuits = $derived(snapshot.circuits === null ? null : Object.entries(snapshot.circuits));
</script>

<PageHeader
  title="Queues"
  description="Shared durable backlog, deliveries awaiting acknowledgement and circuit admission metadata."
>
  {#snippet actions()}<Button
      disabled={snapshot.queues === null}
      onclick={() =>
        exportJson('queues', {
          queues,
          complete: snapshot.queues_complete,
          circuits: snapshot.circuits,
        })}>Export queues</Button
    >{/snippet}
</PageHeader>
<div class="metrics">
  <MetricCard
    label="Total backlog"
    value={number(snapshot.backlog)}
    hint={snapshot.queues_complete
      ? 'Shared queue coverage is complete'
      : 'Coverage is partial or unavailable'}
    tone={snapshot.queues_complete ? 'normal' : 'warning'}
  />
  <MetricCard
    label="Retained dead letters"
    value={number(snapshot.dead_letters)}
    hint="Terminal failures requiring review"
  />
  <MetricCard
    label="Pending deliveries"
    value={snapshot.queues === null
      ? '—'
      : number(snapshot.queues.reduce((total, queue) => total + queue.pending, 0))}
    hint="Owned by a consumer, awaiting ACK or NACK"
  />
  <MetricCard
    label="Open circuits"
    value={circuits === null
      ? '—'
      : number(circuits.filter(([, circuit]) => circuit.state === 'open').length)}
    hint={snapshot.features.circuit_breaker
      ? 'Gateway circuit protection'
      : 'Circuit protection is disabled'}
  />
</div>
{#if snapshot.queues === null}
  <Notice
    kind="warning"
    title="Queue data is unavailable"
    message="The shared Redis queue state could not be read. Missing backlog is not treated as zero."
  />
{:else}
  {#if !snapshot.queues_complete}<Notice
      kind="warning"
      title="Queue coverage is partial"
      message="Some stream lag values or queue reads were unavailable. Unknown waiting work is shown explicitly."
    />{/if}
  <FilterBar
    bind:search
    bind:state={stateFilter}
    bind:sort
    searchLabel="Find a stream"
    placeholder="Stream identity"
    stateLabel="Work state"
    stateOptions={[
      { value: '', label: 'All queues' },
      { value: 'nonempty', label: 'Nonempty' },
      { value: 'pending', label: 'Pending delivery' },
      { value: 'waiting', label: 'Waiting for a consumer' },
      { value: 'unknown', label: 'Unknown waiting count' },
    ]}
    sortOptions={[
      { value: 'pending', label: 'Most pending work' },
      { value: 'stream', label: 'Stream identity' },
    ]}
  >
    <Button
      variant="quiet"
      onclick={() => {
        search = '';
        stateFilter = '';
        sort = 'pending';
        page = 1;
      }}>Reset filters</Button
    >
  </FilterBar>
  <Table
    headings={['Stream', 'Pending ACK / NACK', 'Waiting for delivery']}
    caption="Shared stream backlog and pending acknowledgements"
    empty={queues.length === 0}
  >
    {#each visible as queue (queue.stream)}<tr
        ><td class="wrap">{queue.stream}</td><td>{number(queue.pending)}</td><td
          >{queue.waiting === null ? 'Unknown' : number(queue.waiting)}</td
        ></tr
      >{/each}
  </Table>
  <Pagination bind:page total={queues.length} />
{/if}
<Panel title="Circuit admission" description="Gateway protection">
  {#if circuits === null}<p class="muted small">Circuit metadata is unavailable.</p>{:else}
    <Table
      headings={[
        'Target',
        'Circuit',
        'Admission',
        'Failures / successes',
        'Last failure',
        'Opened',
      ]}
      caption="Circuit breaker state and admission metadata"
      empty={circuits.length === 0}
      emptyMessage="No circuit records were reported."
    >
      {#each circuits as [target, circuit] (target)}<tr
          ><td>{target}</td><td><StatusBadge status={circuit.state} /></td><td
            >{circuit.allowed ? 'Allowed' : 'Blocked'}</td
          ><td>{number(circuit.failure_count)} / {number(circuit.success_count)}</td><td
            >{timestamp(circuit.last_failure_time)}</td
          ><td>{timestamp(circuit.opened_at)}</td></tr
        >{/each}
    </Table>
  {/if}
</Panel>
<p class="scope-note">{snapshot.scope}</p>
