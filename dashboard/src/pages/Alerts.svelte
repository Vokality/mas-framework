<script lang="ts">
  import type { ManagementSnapshot } from '../lib/contracts';
  import { exportJson, timestamp } from '../lib/format';
  import PageHeader from '../components/PageHeader.svelte';
  import Button from '../components/Button.svelte';
  import FilterBar from '../components/FilterBar.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Pagination from '../components/Pagination.svelte';
  import Notice from '../components/Notice.svelte';
  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let search = $state(''),
    selectedState = $state('active'),
    sort = $state('severity'),
    severity = $state(''),
    page = $state(1);
  const ranks = { critical: 0, warning: 1, info: 2 };
  let filtered = $derived(
    (snapshot.fleet?.alerts ?? [])
      .filter(
        (alert) =>
          (!selectedState || alert.status === selectedState) &&
          (!severity || alert.severity === severity) &&
          `${alert.title} ${alert.detail} ${alert.kind}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      )
      .toSorted((a, b) =>
        sort === 'severity'
          ? ranks[a.severity] - ranks[b.severity] || b.updated_at - a.updated_at
          : b.updated_at - a.updated_at,
      ),
  );
  let current = $derived(Math.min(page, Math.max(1, Math.ceil(filtered.length / 12))));
  $effect(() => {
    search;
    selectedState;
    severity;
    sort;
    page = 1;
  });
</script>

<PageHeader
  title="Operational alerts"
  description="Retained health, freshness, export, coverage, and SLO conditions."
  >{#snippet actions()}<Button
      disabled={!filtered.length}
      onclick={() => exportJson('alerts', filtered)}>↓ Export selection</Button
    >{/snippet}</PageHeader
>
{#if !snapshot.fleet}<Notice
    title="Alert observations unavailable"
    message="Shared fleet state is unavailable; current conditions cannot be assessed."
    kind="warning"
  />{/if}
<FilterBar
  bind:search
  bind:state={selectedState}
  bind:sort
  placeholder="Condition, detail, or category"
  stateOptions={[
    { value: 'active', label: 'Active' },
    { value: 'resolved', label: 'Resolved' },
    { value: '', label: 'All states' },
  ]}
  sortOptions={[
    { value: 'severity', label: 'Severity first' },
    { value: 'newest', label: 'Recently updated' },
  ]}
>
  <label
    ><span class="field-label">Severity</span><select bind:value={severity}
      ><option value="">All severities</option><option value="critical">Critical</option><option
        value="warning">Warning</option
      ><option value="info">Info</option></select
    ></label
  >
</FilterBar>
<div class="alert-grid">
  {#each filtered.slice((current - 1) * 12, current * 12) as alert}<article
      class="alert-card {alert.severity}"
      class:resolved={alert.status === 'resolved'}
    >
      <div class="card-top">
        <StatusBadge status={alert.severity} /><StatusBadge
          status={alert.status}
          tone={alert.status === 'active' ? 'warn' : 'good'}
        />
      </div>
      <h2>{alert.title}</h2>
      <p>{alert.detail}</p>
      <div class="small muted">
        Opened {timestamp(alert.opened_at)}<br />Updated {timestamp(
          alert.updated_at,
        )}{#if alert.resolved_at}<br />Resolved {timestamp(alert.resolved_at)}{/if}
      </div>
      <details>
        <summary>Condition identity</summary>
        <div class="metadata">{alert.alert_id}<br />{alert.kind}</div>
      </details>
    </article>{/each}
</div>
{#if !filtered.length}<div class="empty">No retained conditions match these filters.</div>{/if}
<Pagination bind:page total={filtered.length} size={12} />
<p class="scope-note">
  Conditions are evaluated and retained by the runtime. This view does not acknowledge conditions or
  send notifications.
</p>
