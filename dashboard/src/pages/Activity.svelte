<script lang="ts">
  import Button from '../components/Button.svelte';
  import FilterBar from '../components/FilterBar.svelte';
  import MetricCard from '../components/MetricCard.svelte';
  import Notice from '../components/Notice.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import Pagination from '../components/Pagination.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import Table from '../components/Table.svelte';
  import type { ManagementSnapshot } from '../lib/contracts';
  import { exportJson, milliseconds, number, timestamp } from '../lib/format';

  let { snapshot }: { snapshot: ManagementSnapshot } = $props();
  let search = $state('');
  let stateFilter = $state('');
  let sort = $state('newest');
  let page = $state(1);
  let decisions = $derived(
    [...new Set(snapshot.recent_activity?.map((event) => event.decision) ?? [])].sort(),
  );
  let activity = $derived.by(() => {
    const needle = search.trim().toLowerCase();
    return (snapshot.recent_activity ?? [])
      .filter(
        (event) =>
          (!stateFilter || event.decision === stateFilter) &&
          (!needle ||
            [
              event.message_id,
              event.sender_id,
              event.target_id,
              event.message_type ?? '',
              event.correlation_id ?? '',
              ...event.violations,
            ].some((value) => value.toLowerCase().includes(needle))),
      )
      .sort((a, b) =>
        sort === 'oldest'
          ? a.timestamp - b.timestamp
          : sort === 'latency'
            ? b.latency_ms - a.latency_ms
            : b.timestamp - a.timestamp,
      );
  });
  let visible = $derived(
    activity.slice(
      (Math.min(page, Math.max(1, Math.ceil(activity.length / 20))) - 1) * 20,
      Math.min(page, Math.max(1, Math.ceil(activity.length / 20))) * 20,
    ),
  );
</script>

<PageHeader
  title="Activity"
  description="Recent persisted authorization decisions and safe message metadata. Business payloads are excluded."
>
  {#snippet actions()}<Button
      disabled={snapshot.recent_activity === null}
      onclick={() => exportJson('activity', activity)}>Export activity</Button
    >{/snippet}
</PageHeader>
{#if snapshot.recent_activity === null}
  <Notice
    kind="warning"
    title="Policy activity is unavailable"
    message="The retained audit decisions could not be read. No successful or rejected messages are inferred from missing data."
  />
{:else}
  <div class="metrics three">
    <MetricCard
      label="Recent decisions"
      value={number(snapshot.recent_activity.length)}
      hint="Bounded recent audit view"
    />
    <MetricCard
      label="Allowed decisions"
      value={number(
        snapshot.recent_activity.filter((event) => event.decision === 'ALLOWED').length,
      )}
      hint="Authorization, not proof of handler completion"
    />
    <MetricCard
      label="Decisions with violations"
      value={number(snapshot.recent_activity.filter((event) => event.violations.length > 0).length)}
      hint="Recorded policy violation labels"
    />
  </div>
  <FilterBar
    bind:search
    bind:state={stateFilter}
    bind:sort
    searchLabel="Find an event"
    placeholder="Message, sender, target, type or correlation"
    stateLabel="Decision"
    stateOptions={[
      { value: '', label: 'All decisions' },
      ...decisions.map((value) => ({ value, label: value })),
    ]}
    sortOptions={[
      { value: 'newest', label: 'Newest first' },
      { value: 'oldest', label: 'Oldest first' },
      { value: 'latency', label: 'Highest policy latency' },
    ]}
  >
    <Button
      variant="quiet"
      onclick={() => {
        search = '';
        stateFilter = '';
        sort = 'newest';
        page = 1;
      }}>Reset filters</Button
    >
  </FilterBar>
  <Table
    headings={['Time', 'Message', 'Sender → target', 'Decision', 'Policy latency', 'Metadata']}
    caption="Recent authorization decision metadata"
    empty={activity.length === 0}
  >
    {#each visible as event}
      <tr>
        <td>{timestamp(event.timestamp)}</td>
        <td class="wrap"
          >{event.message_id}
          <div class="small">{event.message_type ?? 'No message type'}</div></td
        >
        <td>{event.sender_id} → {event.target_id}</td>
        <td><StatusBadge status={event.decision} /></td>
        <td>{milliseconds(event.latency_ms)}</td>
        <td
          ><details>
            <summary>Decision detail</summary>
            <div class="metadata">
              Correlation: {event.correlation_id ?? 'None'}
              <div>
                Violations: {event.violations.length ? event.violations.join(', ') : 'None'}
              </div>
            </div>
          </details></td
        >
      </tr>
    {/each}
  </Table>
  <Pagination bind:page total={activity.length} />
  <p class="scope-note">
    These are authorization decisions, not end-to-end completion records. <a href="/traces"
      >Explore retained traces</a
    > for actual span coverage and delivery timing.
  </p>
{/if}
