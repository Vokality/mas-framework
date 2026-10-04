<script lang="ts">
  let {
    label,
    value,
    hint,
    tone = 'normal',
  }: {
    label: string;
    value: string;
    hint?: string;
    tone?: 'normal' | 'hero' | 'warning';
  } = $props();
</script>

<article class="metric-card {tone}">
  <div class="metric-label">{label}</div>
  <div class="metric-value">{value}</div>
  {#if hint}<div class="metric-hint">{hint}</div>{/if}
</article>
