<script lang="ts">
  import type { Snippet } from 'svelte';
  let {
    children,
    onclick,
    disabled = false,
    variant = 'normal',
    type = 'button',
    title,
  }: {
    children: Snippet;
    onclick?: () => void;
    disabled?: boolean;
    variant?: 'normal' | 'primary' | 'quiet';
    type?: 'button' | 'submit';
    title?: string;
  } = $props();
</script>

<button class="button {variant}" {type} {onclick} {disabled} {title}>{@render children()}</button>
