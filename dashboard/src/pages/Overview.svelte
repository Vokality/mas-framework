<script lang="ts">
  import type { ManagementSnapshot } from '../lib/contracts';
  import { duration, milliseconds, number, percent } from '../lib/format';
  import PageHeader from '../components/PageHeader.svelte';
  import MetricCard from '../components/MetricCard.svelte';
  import Panel from '../components/Panel.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Notice from '../components/Notice.svelte';
  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let fleet = $derived(snapshot.fleet);
  let activeAlerts = $derived(fleet?.alerts.filter((alert) => alert.status === 'active') ?? []);
  let instances = $derived(snapshot.agents.reduce((sum, agent) => sum + agent.sessions.length, 0));
</script>

<PageHeader
  title="System overview"
  description="The signals that need attention. Explore each area for details and controls."
/>
{#if !fleet}<Notice
    title="Shared fleet observations unavailable"
    message="The local broker snapshot is available below. Fleet health and performance cannot be assessed."
    kind="warning"
  />{/if}
<div class="metrics three">
  <MetricCard
    label="Fleet health"
    value={fleet?.status ?? 'Unknown'}
    hint={fleet
      ? `${fleet.brokers.filter((broker) => broker.fresh).length} / ${fleet.brokers.length} fresh brokers · ${fleet.complete ? 'complete' : 'partial'} observation coverage`
      : 'No shared heartbeat data'}
    tone="hero"
  />
  <MetricCard
    label="Accepted messages / sec"
    value={number(fleet?.performance.accepted_rate)}
    hint={fleet
      ? `Target ≥ ${number(fleet.targets.accepted_rate)} / sec · ${number(fleet.performance.finished_at - fleet.performance.started_at)}s counter interval`
      : 'Awaiting measured rate'}
    tone="hero"
  />
  <MetricCard
    label="Send → delivery p95"
    value={milliseconds(fleet?.performance.end_to_end_p95_ms)}
    hint={fleet
      ? `Target < ${number(fleet.targets.end_to_end_p95_ms)} ms · ${percent(fleet.performance.latency_coverage)} joined coverage`
      : 'Awaiting correlated deliveries'}
    tone="hero"
  />
</div>
<div class="metrics">
  <MetricCard
    label="Active conditions"
    value={fleet ? number(activeAlerts.length) : '—'}
    hint={fleet ? 'Retained fleet alerts' : 'Fleet alert state unavailable'}
    tone={activeAlerts.length ? 'warning' : 'normal'}
  />
  <MetricCard
    label="Connected instances"
    value={number(instances)}
    hint="This broker's local sessions"
  />
  <MetricCard
    label="Waiting / pending"
    value={number(snapshot.backlog)}
    hint={snapshot.queues_complete ? 'Shared durable delivery queues' : 'Queue coverage incomplete'}
  />
  <MetricCard
    label="Dead letters"
    value={number(snapshot.dead_letters)}
    hint="Shared retained undeliverable work"
  />
</div>
<div class="panels">
  <Panel title="Needs attention">
    {#if activeAlerts.length}{#each activeAlerts.slice(0, 4) as alert}<a
          class="quick-link"
          href="/alerts"
          ><div>
            <StatusBadge status={alert.severity} />
            <p style="margin-top:8px">{alert.title}</p>
          </div>
          <span>↗</span></a
        >{/each}{:else}<div class="empty">
        {fleet
          ? 'No active conditions in retained observations.'
          : 'Fleet alert state unavailable.'}
      </div>{/if}
    <a class="quick-link" href="/alerts">Inspect all alerts <span>→</span></a>
  </Panel>
  <Panel title="Local broker" description={snapshot.broker_id ?? 'Process snapshot'}>
    <div class="metric-row">
      <span>Readiness</span><StatusBadge status={snapshot.health.status} />
    </div>
    <div class="metric-row">
      <span>Redis</span><strong
        >{snapshot.health.redis_available
          ? milliseconds(snapshot.health.redis_latency_ms)
          : 'Unavailable'}</strong
      >
    </div>
    <div class="metric-row">
      <span>Uptime</span><strong>{duration(snapshot.uptime_seconds)}</strong>
    </div>
    <div class="metric-row">
      <span>Policy mean / max</span><strong
        >{milliseconds(snapshot.telemetry.policy_latency_mean_ms)} / {milliseconds(
          snapshot.telemetry.policy_latency_max_ms,
        )}</strong
      >
    </div>
    <p class="scope-note">{snapshot.scope}</p>
  </Panel>
</div>
<div class="panels">
  <Panel title="Explore the runtime"
    ><a class="quick-link" href="/fleet">Broker health & freshness <span>→</span></a><a
      class="quick-link"
      href="/agents">Agent capabilities & instances <span>→</span></a
    ><a class="quick-link" href="/queues">Backlog & circuit state <span>→</span></a></Panel
  >
  <Panel title="Follow a message"
    ><a class="quick-link" href="/performance">Throughput & latency history <span>→</span></a><a
      class="quick-link"
      href="/traces">Trace timing & correlation <span>→</span></a
    ><a class="quick-link" href="/activity">Admission decisions <span>→</span></a></Panel
  >
</div>
