<script lang="ts">
  import Button from './Button.svelte';
  let {
    page = $bindable(1),
    total,
    size = 20,
  }: { page?: number; total: number; size?: number } = $props();
  let count = $derived(Math.max(1, Math.ceil(total / size)));
  let current = $derived(Math.min(page, count));
</script>

<div class="pagination">
  <span
    >{total
      ? `${(current - 1) * size + 1}–${Math.min(current * size, total)} of ${total}`
      : '0 records'}</span
  >
  <div>
    <Button disabled={current <= 1} onclick={() => (page = current - 1)}>← Previous</Button><span
      class="page-number">{current} / {count}</span
    ><Button disabled={current >= count} onclick={() => (page = current + 1)}>Next →</Button>
  </div>
</div>
