<script lang="ts">
  import Button from '../components/Button.svelte';
  import FilterBar from '../components/FilterBar.svelte';
  import MetricCard from '../components/MetricCard.svelte';
  import Notice from '../components/Notice.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import Pagination from '../components/Pagination.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import type { ManagementSnapshot } from '../lib/contracts';
  import { exportJson, number } from '../lib/format';

  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let search = $state('');
  let stateFilter = $state('');
  let capability = $state('');
  let sort = $state('agent');
  let page = $state(1);
  let capabilities = $derived(
    [...new Set(snapshot.agents.flatMap((agent) => agent.capabilities))].sort(),
  );
  let agents = $derived.by(() => {
    const needle = search.trim().toLowerCase();
    return snapshot.agents
      .filter(
        (agent) =>
          (!stateFilter || agent.status === stateFilter) &&
          (!capability || agent.capabilities.includes(capability)) &&
          (!needle ||
            [
              agent.agent_id,
              ...agent.capabilities,
              ...agent.sessions.map((session) => session.instance_id),
            ].some((value) => value.toLowerCase().includes(needle))),
      )
      .sort((a, b) =>
        sort === 'sessions'
          ? b.sessions.length - a.sessions.length || a.agent_id.localeCompare(b.agent_id)
          : sort === 'status'
            ? a.status.localeCompare(b.status) || a.agent_id.localeCompare(b.agent_id)
            : a.agent_id.localeCompare(b.agent_id),
      );
  });
  let visible = $derived(
    agents.slice(
      (Math.min(page, Math.max(1, Math.ceil(agents.length / 20))) - 1) * 20,
      Math.min(page, Math.max(1, Math.ceil(agents.length / 20))) * 20,
    ),
  );
</script>

<PageHeader
  title="Agents"
  description="Configured identities, shared lease availability and the agent instances connected to this broker."
>
  {#snippet actions()}<Button onclick={() => exportJson('agents', agents)}>Export agents</Button
    >{/snippet}
</PageHeader>
<div class="metrics">
  <MetricCard
    label="Configured identities"
    value={number(snapshot.agents.length)}
    hint="Broker allowlist"
  />
  <MetricCard
    label="Shared active agents"
    value={number(snapshot.agents.filter((agent) => agent.status === 'active').length)}
    hint="Derived from live Redis leases"
  />
  <MetricCard
    label="Local instances"
    value={number(snapshot.agents.reduce((total, agent) => total + agent.sessions.length, 0))}
    hint={snapshot.broker_id ?? 'Current broker'}
  />
  <MetricCard
    label="Local in-flight work"
    value={number(
      snapshot.agents.reduce(
        (total, agent) =>
          total + agent.sessions.reduce((count, session) => count + session.inflight, 0),
        0,
      ),
    )}
    hint="Awaiting ACK or NACK"
  />
</div>
<FilterBar
  bind:search
  bind:state={stateFilter}
  bind:sort
  searchLabel="Find an agent"
  placeholder="Agent, capability or instance"
  stateLabel="Availability"
  stateOptions={[
    { value: '', label: 'All availability states' },
    { value: 'active', label: 'Active' },
    { value: 'inactive', label: 'Inactive' },
    { value: 'degraded', label: 'Degraded' },
    { value: 'unknown', label: 'Unknown' },
  ]}
  sortOptions={[
    { value: 'agent', label: 'Agent identity' },
    { value: 'sessions', label: 'Most local instances' },
    { value: 'status', label: 'Availability' },
  ]}
>
  <label
    ><span class="field-label">Capability</span><select bind:value={capability}
      ><option value="">All capabilities</option>{#each capabilities as value}<option {value}
          >{value}</option
        >{/each}</select
    ></label
  >
  <Button
    variant="quiet"
    onclick={() => {
      search = '';
      stateFilter = '';
      capability = '';
      sort = 'agent';
      page = 1;
    }}>Reset filters</Button
  >
</FilterBar>
{#if agents.length === 0}<Notice
    title="No matching agents"
    message="Change the search or availability and capability filters."
  />{/if}
<div class="agent-grid">
  {#each visible as agent (agent.agent_id)}
    <article class="agent-card">
      <div class="card-top">
        <h2 class="wrap">{agent.agent_id}</h2>
        <StatusBadge status={agent.status} />
      </div>
      <div class="feature-list">
        {#each agent.capabilities as value}<span class="feature">{value}</span>{:else}<span
            class="small muted">No declared capabilities</span
          >{/each}
      </div>
      <div class="metric-row">
        <span>Local instances</span><strong>{number(agent.sessions.length)}</strong>
      </div>
      <div class="metric-row">
        <span>In flight</span><strong
          >{number(agent.sessions.reduce((total, session) => total + session.inflight, 0))}</strong
        >
      </div>
      <details>
        <summary>Connected instances ({agent.sessions.length})</summary>
        {#each agent.sessions as session (session.instance_id)}
          <div class="metadata">
            <div class="wrap"><strong>{session.instance_id}</strong></div>
            <div>{number(session.inflight)} in flight · {number(session.outbound)} outbound</div>
            <StatusBadge
              status={session.worker_running ? 'healthy' : 'stopped'}
              label={session.worker_running ? 'Worker running' : 'Worker stopped'}
            />
          </div>
        {:else}
          <p class="small muted">
            No instances connected to this broker. Shared active leases may belong to another fleet
            member.
          </p>
        {/each}
      </details>
    </article>
  {/each}
</div>
<Pagination bind:page total={agents.length} />
<p class="scope-note">{snapshot.scope}</p>
