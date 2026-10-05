<script setup lang="ts">
defineProps<{ title?: string; value: unknown }>();
</script>
<template>
  <details class="raw">
    <summary>{{ title || "展开原始记录" }}</summary>
    <pre>{{ JSON.stringify(value, null, 2) }}</pre>
  </details>
</template>
