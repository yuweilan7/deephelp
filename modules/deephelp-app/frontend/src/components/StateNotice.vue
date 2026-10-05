<script setup lang="ts">
defineProps<{
  error?: string;
  loading?: boolean;
  empty?: boolean;
  next?: string;
}>();
</script>
<template>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    role="alert"
  />
  <p v-else-if="loading" role="status">正在读取实际记录…</p>
  <el-empty
    v-else-if="empty"
    :description="next || '尚无记录，请先完成本页操作。'"
  />
</template>
