<script lang="ts">
  import type { Snippet } from 'svelte';
  let { title, description, actions }: { title: string; description: string; actions?: Snippet } =
    $props();
</script>

<header class="page-heading">
  <div>
    <h1>{title}</h1>
    <p>{description}</p>
  </div>
  {#if actions}<div class="page-actions">{@render actions()}</div>{/if}
</header>
