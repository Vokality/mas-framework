<script lang="ts">
  import type { Snippet } from 'svelte';
  let {
    headings,
    children,
    empty = false,
    emptyMessage = 'No matching records.',
    caption,
  }: {
    headings: readonly string[];
    children: Snippet;
    empty?: boolean;
    emptyMessage?: string;
    caption: string;
  } = $props();
</script>

<div class="table-wrap" role="region" aria-label={caption}>
  <table>
    <caption class="sr-only">{caption}</caption><thead
      ><tr
        >{#each headings as heading}<th scope="col">{heading}</th>{/each}</tr
      ></thead
    ><tbody
      >{#if empty}<tr><td colspan={headings.length} class="empty">{emptyMessage}</td></tr
        >{:else}{@render children()}{/if}</tbody
    >
  </table>
</div>
