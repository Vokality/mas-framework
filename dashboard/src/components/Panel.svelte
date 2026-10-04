<script lang="ts">
  import type { Snippet } from 'svelte';
  let {
    title,
    description,
    children,
    actions,
  }: { title: string; description?: string; children: Snippet; actions?: Snippet } = $props();
</script>

<section class="panel">
  <div class="panel-heading">
    <div>
      <h2>{title}</h2>
      {#if description}<p class="panel-description">{description}</p>{/if}
    </div>
    {#if actions}<div>{@render actions()}</div>{/if}
  </div>
  {@render children()}
</section>
