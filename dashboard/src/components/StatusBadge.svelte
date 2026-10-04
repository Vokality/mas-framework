<script lang="ts">
  let {
    status,
    label = status,
    tone: override,
  }: {
    status: string;
    label?: string;
    tone?: 'good' | 'bad' | 'warn' | 'neutral';
  } = $props();
  let normalized = $derived(status.toLowerCase());
  let tone = $derived(
    override ??
      (['healthy', 'active', 'complete', 'allowed', 'closed', 'resolved'].includes(normalized)
        ? 'good'
        : ['degraded', 'stopped', 'critical', 'denied', 'open', 'failed', 'dlp_blocked'].includes(
              normalized,
            )
          ? 'bad'
          : [
                'stale',
                'warning',
                'partial',
                'pending',
                'half_open',
                'alert',
                'rate_limited',
                'dlp_redacted',
              ].includes(normalized)
            ? 'warn'
            : 'neutral'),
  );
</script>

<span class="status-badge {tone}"><span class="status-dot" aria-hidden="true"></span>{label}</span>
