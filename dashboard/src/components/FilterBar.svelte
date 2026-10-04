<script lang="ts">
  import type { Snippet } from 'svelte';
  let {
    search = $bindable(''),
    state = $bindable(''),
    sort = $bindable(''),
    searchLabel = 'Search',
    placeholder = 'Search records',
    stateLabel = 'State',
    stateOptions = [],
    sortOptions = [],
    children,
  }: {
    search?: string;
    state?: string;
    sort?: string;
    searchLabel?: string;
    placeholder?: string;
    stateLabel?: string;
    stateOptions?: readonly { value: string; label: string }[];
    sortOptions?: readonly { value: string; label: string }[];
    children?: Snippet;
  } = $props();
</script>

<div class="filter-bar">
  <label class="search-field"
    ><span class="field-label">{searchLabel}</span><input
      type="search"
      bind:value={search}
      {placeholder}
    /></label
  >
  {#if stateOptions.length}<label
      ><span class="field-label">{stateLabel}</span><select bind:value={state}
        >{#each stateOptions as option}<option value={option.value}>{option.label}</option
          >{/each}</select
      ></label
    >{/if}
  {#if sortOptions.length}<label
      ><span class="field-label">Sort</span><select bind:value={sort}
        >{#each sortOptions as option}<option value={option.value}>{option.label}</option
          >{/each}</select
      ></label
    >{/if}
  {#if children}{@render children()}{/if}
</div>
