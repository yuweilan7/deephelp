<script setup lang="ts">
defineProps<{
  title: string;
  goal: string;
  needs: string;
  steps: string[];
  expected: string;
  lesson: string;
}>();
</script>
<template>
  <header class="page-guide">
    <p class="eyebrow">DEEPHELP · 运行与学习</p>
    <h1>{{ title }}</h1>
    <p>{{ goal }}</p>
    <details>
      <summary>操作指引与预期观察</summary>
      <p>需要：{{ needs }}</p>
      <ol>
        <li v-for="s in steps" :key="s">{{ s }}</li>
      </ol>
      <p>预期：{{ expected }}</p>
      <a :href="lesson" target="_blank" rel="noopener">打开相关教程</a>
    </details>
  </header>
</template>
