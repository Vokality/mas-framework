<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { Dashboard } from './lib/dashboard.svelte';
  import { pages, readRoute } from './lib/router';
  import { timestamp } from './lib/format';
  import Button from './components/Button.svelte';
  import StatusBadge from './components/StatusBadge.svelte';
  import Notice from './components/Notice.svelte';
  import Panel from './components/Panel.svelte';
  import Overview from './pages/Overview.svelte';
  import Fleet from './pages/Fleet.svelte';
  import Performance from './pages/Performance.svelte';
  import Traces from './pages/Traces.svelte';
  import TraceDetail from './pages/TraceDetail.svelte';
  import Alerts from './pages/Alerts.svelte';
  import Agents from './pages/Agents.svelte';
  import Queues from './pages/Queues.svelte';
  import Activity from './pages/Activity.svelte';
  import Telemetry from './pages/Telemetry.svelte';

  const dashboard = new Dashboard();
  let route = $state(readRoute(location.pathname));
  let paused = $state(false),
    cadence = $state(4),
    tokenInput = $state('');
  let historyLimit = $state(120),
    traceLimit = $state(100);
  let theme = $state<'paper' | 'ink'>('paper');
  let activeAlerts = $derived(
    dashboard.snapshot?.fleet?.alerts.filter((alert) => alert.status === 'active').length ?? 0,
  );
  let activePage = $derived(pages.find((page) => page.id === route?.page));

  function navigate(event: MouseEvent): void {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      !(event.target instanceof Element)
    )
      return;
    const anchor = event.target.closest('a');
    if (!(anchor instanceof HTMLAnchorElement) || anchor.target || anchor.download) return;
    const url = new URL(anchor.href);
    const next = readRoute(url.pathname);
    if (url.origin !== location.origin || !next || url.hash) return;
    event.preventDefault();
    if (url.pathname === location.pathname) return;
    history.pushState(null, '', url.pathname);
    route = next;
    document.getElementById('main-content')?.focus();
    window.scrollTo({ top: 0 });
  }
  function syncRoute(): void {
    route = readRoute(location.pathname);
  }
  function connect(event: SubmitEvent): void {
    event.preventDefault();
    dashboard.connect(tokenInput.trim());
    tokenInput = '';
  }

  onMount(() => {
    try {
      const saved = localStorage.getItem('mas-theme');
      if (saved === 'paper' || saved === 'ink') theme = saved;
    } catch {
      /* Theme preference is optional. */
    }
    void dashboard.refresh();
    return () => dashboard.stop();
  });
  $effect(() => {
    const current = route,
      historyCount = historyLimit,
      traceCount = traceLimit;
    if (current) untrack(() => dashboard.select(current, historyCount, traceCount));
    else untrack(() => dashboard.stop());
  });
  $effect(() => {
    const seconds = cadence,
      stopped = paused;
    if (stopped) return;
    const timer = setInterval(() => void dashboard.refresh(), seconds * 1000);
    return () => clearInterval(timer);
  });
  $effect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem('mas-theme', theme);
    } catch {
      /* Theme preference is optional. */
    }
  });
</script>

<svelte:window onpopstate={syncRoute} onclick={navigate} />
<svelte:document
  onvisibilitychange={() => {
    if (!document.hidden && !paused) void dashboard.refresh();
  }}
/>
<svelte:head
  ><title>MAS · {route?.traceId ? 'Trace detail' : (activePage?.label ?? 'Page not found')}</title
  ></svelte:head
>
<a class="skip-link" href="#main-content">Skip to content</a>
<aside class="sidebar">
  <div>
    <a class="brand" href="/overview" aria-label="MAS overview">MAS<span>_</span></a>
  </div>
  <nav aria-label="Management pages">
    {#each pages as page, index}{#if index === 0 || page.group !== pages[index - 1].group}<div
          class="nav-group"
        >
          {page.group}
        </div>{/if}<a
        href={`/${page.id}`}
        aria-current={route?.page === page.id ? 'page' : undefined}
        >{page.label}{#if page.id === 'alerts' && activeAlerts}<span class="nav-count"
            >{activeAlerts}</span
          >{/if}</a
      >{/each}
  </nav>
  <div class="sidebar-foot">
    Read-only management<br />Shared fleet / local sessions
  </div>
</aside>
<main class="main" id="main-content" tabindex="-1">
  <div class="topbar">
    <div class="topbar-left">
      <StatusBadge
        status={dashboard.snapshotError
          ? 'degraded'
          : (dashboard.snapshot?.health.status ?? 'unknown')}
        label={dashboard.snapshotError
          ? 'Connection lost'
          : (dashboard.snapshot?.health.status ?? 'Connecting')}
      /><span class="small muted">{dashboard.snapshot?.broker_id ?? 'Management listener'}</span
      ><span class="snapshot-time small muted"
        >{dashboard.snapshot
          ? timestamp(dashboard.snapshot.generated_at)
          : 'Awaiting snapshot'}</span
      >
    </div>
    <div class="topbar-controls">
      <label
        >Refresh<select bind:value={cadence} aria-label="Refresh cadence"
          ><option value={2}>2 sec</option><option value={4}>4 sec</option><option value={10}
            >10 sec</option
          ><option value={30}>30 sec</option></select
        ></label
      ><Button onclick={() => (paused = !paused)}>{paused ? '▶ Resume' : 'Ⅱ Pause'}</Button><Button
        onclick={() => dashboard.refresh()}
        disabled={dashboard.loading || dashboard.accessRequired}
        >{dashboard.loading ? 'Refreshing…' : '↻ Refresh'}</Button
      ><Button
        title="Toggle paper / ink theme"
        onclick={() => (theme = theme === 'paper' ? 'ink' : 'paper')}
        >{theme === 'paper' ? '◐ Ink' : '◐ Paper'}</Button
      ><Button variant="quiet" onclick={() => dashboard.disconnect()}>Access</Button>
    </div>
  </div>
  {#if dashboard.accessRequired}
    <div class="login">
      <Panel title="Management access" description="Reader authentication"
        ><p>
          {dashboard.accessMessage} Use a token issued by your identity provider with the management scope
          and operator role, or a local development reader token.
        </p>
        <form onsubmit={connect}>
          <label for="reader-token">Reader access token</label><input
            id="reader-token"
            type="password"
            autocomplete="off"
            bind:value={tokenInput}
            required
          /><Button type="submit" variant="primary">Connect</Button>
          <Button
            onclick={() => {
              tokenInput = '';
              dashboard.connect('');
            }}>Use local access</Button
          >
          <p class="small">Credentials remain in memory and are cleared on reload.</p>
        </form></Panel
      >
    </div>
  {:else if !route}<Notice
      title="Page not found"
      message="Choose a management page from the navigation."
      kind="error"
    />
  {:else}
    {#if dashboard.snapshotError}<Notice
        title={dashboard.snapshot ? 'Displayed snapshot is stale' : 'Snapshot unavailable'}
        message={dashboard.snapshotError}
        kind="error"
      />{/if}
    {#if dashboard.snapshot}
      {#key route.page}<div class="page-content">
          {#if route.page === 'overview'}<Overview snapshot={dashboard.snapshot} />
          {:else if route.page === 'fleet'}<Fleet snapshot={dashboard.snapshot} />
          {:else if route.page === 'performance'}<Performance
              snapshot={dashboard.snapshot}
              history={dashboard.history}
              error={dashboard.historyError}
              bind:limit={historyLimit}
            />
          {:else if route.page === 'traces'}{#if route.traceId}<TraceDetail
                traceId={route.traceId}
                detail={dashboard.detail}
                error={dashboard.detailError}
              />{:else}<Traces
                snapshot={dashboard.snapshot}
                traces={dashboard.traces}
                error={dashboard.tracesError}
                bind:limit={traceLimit}
              />{/if}
          {:else if route.page === 'alerts'}<Alerts snapshot={dashboard.snapshot} />
          {:else if route.page === 'agents'}<Agents snapshot={dashboard.snapshot} />
          {:else if route.page === 'queues'}<Queues snapshot={dashboard.snapshot} />
          {:else if route.page === 'activity'}<Activity snapshot={dashboard.snapshot} />
          {:else if route.page === 'telemetry'}<Telemetry snapshot={dashboard.snapshot} />{/if}
        </div>{/key}
    {:else if !dashboard.snapshotError}<div class="loading" role="status">
        Connecting to the MAS runtime…
      </div>{/if}
  {/if}
  <footer class="page-footer">
    <span>MAS / System observability</span><span
      >{paused ? 'Auto-refresh paused' : `Auto-refresh every ${cadence}s`} · {dashboard.snapshot
        ? `Snapshot ${timestamp(dashboard.snapshot.generated_at)}`
        : 'No snapshot'}</span
    >
  </footer>
</main>
