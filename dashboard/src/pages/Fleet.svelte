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
  import type { FleetMember, ManagementSnapshot } from '../lib/contracts';
  import { duration, exportJson, milliseconds, number, timestamp } from '../lib/format';

  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let search = $state('');
  let stateFilter = $state('');
  let sort = $state('broker');
  let page = $state(1);
  let fleet = $derived(snapshot.fleet);
  const statusOrder: Record<FleetMember['status'], number> = {
    stale: 0,
    degraded: 1,
    unknown: 2,
    stopped: 3,
    healthy: 4,
  };
  let members = $derived.by(() => {
    const needle = search.trim().toLowerCase();
    return (fleet?.brokers ?? [])
      .filter(
        (member) =>
          (!stateFilter || member.status === stateFilter) &&
          (!needle ||
            [
              member.observation.broker_id,
              member.observation.instance_id,
              member.observation.grpc_address,
              ...member.observation.issues,
            ].some((value) => value.toLowerCase().includes(needle))),
      )
      .sort((a, b) =>
        sort === 'health'
          ? statusOrder[a.status] - statusOrder[b.status] ||
            a.observation.broker_id.localeCompare(b.observation.broker_id)
          : sort === 'age'
            ? b.age_seconds - a.age_seconds
            : a.observation.broker_id.localeCompare(b.observation.broker_id),
      );
  });
  let visible = $derived(
    members.slice(
      (Math.min(page, Math.max(1, Math.ceil(members.length / 20))) - 1) * 20,
      Math.min(page, Math.max(1, Math.ceil(members.length / 20))) * 20,
    ),
  );
</script>

<PageHeader
  title="Fleet"
  description="Broker incarnations, shared heartbeat freshness and local delivery workers across the system."
>
  {#snippet actions()}<Button
      disabled={fleet === null}
      onclick={() => {
        if (fleet) exportJson('fleet', { ...fleet, brokers: members });
      }}>Export fleet</Button
    >{/snippet}
</PageHeader>

{#if fleet === null}
  <Notice
    kind="warning"
    title="Fleet observations are unavailable"
    message="The local broker snapshot is available, but shared heartbeat data could not be read. No fleet health is inferred."
  />
{:else}
  <div class="metrics">
    <MetricCard
      label="Retained brokers"
      value={number(fleet.brokers.length)}
      hint="Includes stale and stopped incarnations"
    />
    <MetricCard
      label="Fresh heartbeats"
      value={number(fleet.brokers.filter((member) => member.fresh).length)}
      hint={`Stale after ${duration(fleet.stale_after_seconds)}`}
    />
    <MetricCard
      label="Fleet health"
      value={fleet.status}
      hint={fleet.complete ? 'Heartbeat coverage is complete' : 'Heartbeat coverage is incomplete'}
      tone={fleet.complete && fleet.status === 'healthy' ? 'normal' : 'warning'}
    />
    <MetricCard
      label="Shared delivery p95"
      value={milliseconds(fleet.performance.end_to_end_p95_ms)}
      hint={`${number(fleet.performance.latency_samples)} paired deliveries in ${duration(fleet.latency_window_seconds)}`}
    />
  </div>
  {#if !fleet.complete}<Notice
      kind="warning"
      title="Fleet coverage is incomplete"
      message="Missing or stale brokers reduce the shared view. Review each member's heartbeat and status before interpreting fleet totals."
    />{/if}
  <FilterBar
    bind:search
    bind:state={stateFilter}
    bind:sort
    searchLabel="Find a broker"
    placeholder="Broker, incarnation, address or issue"
    stateLabel="Health"
    stateOptions={[
      { value: '', label: 'All health states' },
      { value: 'healthy', label: 'Healthy' },
      { value: 'degraded', label: 'Degraded' },
      { value: 'stale', label: 'Stale' },
      { value: 'stopped', label: 'Stopped' },
      { value: 'unknown', label: 'Unknown' },
    ]}
    sortOptions={[
      { value: 'broker', label: 'Broker identity' },
      { value: 'health', label: 'Needs attention first' },
      { value: 'age', label: 'Oldest heartbeat first' },
    ]}
  >
    <Button
      variant="quiet"
      onclick={() => {
        search = '';
        stateFilter = '';
        sort = 'broker';
        page = 1;
      }}>Reset filters</Button
    >
  </FilterBar>
  <Table
    headings={[
      'Broker / incarnation',
      'Health',
      'Heartbeat',
      'Local transports',
      'Redis',
      'Address',
    ]}
    caption="Fleet member health and heartbeat freshness"
    empty={members.length === 0}
  >
    {#each visible as member (member.observation.broker_id)}
      <tr>
        <td
          >{member.observation.broker_id}
          <div class="small">{member.observation.instance_id}</div></td
        >
        <td
          ><StatusBadge status={member.status} />{#if member.observation.issues.length}<div
              class="small wrap"
            >
              {member.observation.issues.join(' · ')}
            </div>{/if}</td
        >
        <td
          >{duration(member.age_seconds)} ago
          <div class="small">{timestamp(member.observation.observed_at)}</div></td
        >
        <td
          >{number(member.observation.sessions.length)}
          <div class="small">
            {number(
              member.observation.sessions.reduce((total, session) => total + session.inflight, 0),
            )} in flight
          </div></td
        >
        <td
          ><StatusBadge
            status={member.observation.redis_available ? 'healthy' : 'degraded'}
            label={member.observation.redis_available ? 'Available' : 'Unavailable'}
          />
          <div class="small">{milliseconds(member.observation.redis_latency_ms)}</div></td
        >
        <td class="wrap"
          >{member.observation.grpc_address}
          <div class="small">
            {member.observation.management_url ?? 'No management listener'}
          </div></td
        >
      </tr>
    {/each}
  </Table>
  <Pagination bind:page total={members.length} />
  <div class="section-label">
    <h2>Member detail</h2>
    <p>Local sessions and actual exporter outcomes</p>
  </div>
  <div class="agent-grid">
    {#each visible as member (member.observation.broker_id)}
      <article class="agent-card">
        <div class="card-top">
          <h3>{member.observation.broker_id}</h3>
          <StatusBadge status={member.status} />
        </div>
        <div class="metric-row">
          <span>Started</span><strong>{timestamp(member.observation.started_at)}</strong>
        </div>
        <div class="metric-row">
          <span>Heartbeat sequence</span><strong>{number(member.observation.sequence)}</strong>
        </div>
        <div class="metric-row">
          <span>Accepted in this incarnation</span><strong
            >{number(member.observation.counters.accepted_messages)}</strong
          >
        </div>
        <div class="metric-row">
          <span>Counter scope</span><strong
            >{member.observation.counters.scope_complete ? 'Complete' : 'Incomplete'}</strong
          >
        </div>
        {#if !member.observation.counters.scope_complete}<p class="scope-note">
            {number(member.observation.counters.dropped_scope_updates)} scoped updates were dropped.
          </p>{/if}
        <details>
          <summary>Sessions ({member.observation.sessions.length})</summary>
          {#if member.observation.sessions.length === 0}<p class="small muted">
              No local agent transports in this heartbeat.
            </p>{/if}
          {#each member.observation.sessions as session (`${session.agent_id}:${session.instance_id}`)}
            <div class="metadata">
              <strong>{session.agent_id}</strong> / {session.instance_id}
              <div>{number(session.inflight)} in flight · {number(session.outbound)} outbound</div>
              <StatusBadge
                status={session.worker_running ? 'healthy' : 'stopped'}
                label={session.worker_running ? 'Worker running' : 'Worker stopped'}
              />
            </div>
          {/each}
        </details>
        <details>
          <summary>Exporter health ({member.observation.exporters.length})</summary>
          {#if member.observation.exporters.length === 0}<p class="small muted">
              No exporter outcomes were reported.
            </p>{/if}
          {#each member.observation.exporters as exporter (exporter.signal)}
            <div class="metadata">
              <div class="card-top">
                <strong>{exporter.signal}</strong><StatusBadge status={exporter.status} />
              </div>
              <div>
                {number(exporter.successes)} successful / {number(exporter.attempts)} attempted exports
              </div>
              <div>Last success: {timestamp(exporter.last_success_at)}</div>
              {#if exporter.last_error}<div>{exporter.last_error}</div>{/if}
            </div>
          {/each}
        </details>
      </article>
    {/each}
  </div>
  <Panel title="Observation scope" description="Shared Redis view"
    ><p class="scope-note">{fleet.scope}</p>
    <p class="scope-note">{fleet.performance.clock_scope}</p></Panel
  >
{/if}
