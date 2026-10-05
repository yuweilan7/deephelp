<script setup lang="ts">
import {
  mechanisms,
  lessonUrl,
  codeUrl,
  experimentCommand,
  type Mechanism,
} from "../content/mechanisms";
defineProps<{ mechanism: Mechanism }>();
</script>
<template>
  <details class="mechanism-guide">
    <summary>跟着当前记录学：{{ mechanisms[mechanism].question }}</summary>
    <p>
      <a :href="lessonUrl(mechanism)" target="_blank" rel="noopener"
        >读对应M33课程</a
      >
      ·
      <a :href="codeUrl(mechanism)" target="_blank" rel="noopener"
        >读当前函数 {{ mechanisms[mechanism].entry }}</a
      >
    </p>
    <p>从工程根运行最小离线实验，预测结果再核对：</p>
    <code>{{ experimentCommand(mechanism) }}</code>
    <p>
      命令展示不会自动执行；课程固定样本帮助理解机制，当前run仍以自己的记录为准。
    </p>
  </details>
</template>
