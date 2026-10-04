<script lang="ts">
  import type { PerformancePoint } from '../lib/contracts';
  import { number, timestamp } from '../lib/format';
  let {
    points,
    field,
    target,
    label,
    unit,
  }: {
    points: PerformancePoint[];
    field: 'accepted_rate' | 'end_to_end_p95_ms';
    target: number | null;
    label: string;
    unit: string;
  } = $props();
  let values = $derived(points.flatMap((point) => (point[field] == null ? [] : [point[field]])));
  let maximum = $derived(Math.max((target ?? 0) * 1.2, ...values, 1));
  let start = $derived(points[0]?.started_at ?? 0);
  let finish = $derived(points.at(-1)?.finished_at ?? start + 1);
  let path = $derived.by(() => {
    let drawing = '',
      connected = false;
    for (const point of points) {
      const value = point[field];
      if (value == null) {
        connected = false;
        continue;
      }
      const x = 10 + ((point.finished_at - start) / Math.max(finish - start, 0.001)) * 580;
      const y = 155 - (value / maximum) * 140;
      drawing += `${connected ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
      connected = true;
    }
    return drawing;
  });
</script>

<div class="trend-chart">
  <div class="chart-heading">
    <h3>{label}</h3>
    <span class="chart-note">{unit} · max {number(maximum)}</span>
  </div>
  {#if values.length}<svg
      viewBox="0 0 600 170"
      role="img"
      aria-label={`${label}; ${values.length} measured intervals; ${target === null ? 'target unavailable' : `target ${target} ${unit}`}`}
      ><title>{label}; gaps indicate missing data</title><path
        class="chart-gridline"
        d="M10 15H590 M10 85H590 M10 155H590"
      />{#if target !== null}<path
          class="chart-target"
          d={`M10 ${155 - (target / maximum) * 140}H590`}
        />{/if}<path
        class="chart-series"
        d={path}
      />{#each points as point}{#if point[field] != null}<circle
            class="chart-point"
            cx={10 + ((point.finished_at - start) / Math.max(finish - start, 0.001)) * 580}
            cy={155 - (point[field] / maximum) * 140}
            r="2"
            ><title>{timestamp(point.finished_at)} · {number(point[field])} {unit}</title></circle
          >{/if}{/each}</svg
    >{:else}<div class="empty chart-empty">No retained measurements in this window.</div>{/if}
  <div class="chart-axis">
    <span>{points.length ? timestamp(start) : '—'}</span><span
      >{points.length ? timestamp(finish) : '—'}</span
    >
  </div>
  <div class="chart-legend">
    <span>● Observed</span><span class="target-label"
      >{target === null ? 'Target unavailable' : `— Target ${number(target)} ${unit}`}</span
    >
  </div>
</div>
