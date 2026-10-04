<script lang="ts">
  let {
    title,
    message,
    kind = 'info',
  }: { title: string; message?: string; kind?: 'info' | 'warning' | 'error' } = $props();
</script>

<div class="notice {kind}" role={kind === 'error' ? 'alert' : 'status'}>
  <strong>{title}</strong>{#if message}<p>{message}</p>{/if}
</div>
