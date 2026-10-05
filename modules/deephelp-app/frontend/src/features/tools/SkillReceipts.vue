<script setup lang="ts">
import type { SkillReceipt } from "../../api/types";
import RawDetails from "../../components/RawDetails.vue";
defineProps<{ receipts?: SkillReceipt[] }>();
</script>
<template>
  <section>
    <h3>原运行的 Skill 步骤</h3>
    <p>Skill 复用已登记工具；SOP 决定业务分支。下方来自原 run 回执，读取新增模型 0 次。没有回执时不能推断取消已完成或工具未派发；未知结果先核对原调用。</p>
    <p v-if="!receipts?.length">所选记录没有 Skill 回执；旧记录或缺槽路径不会补造执行。</p>
    <div v-for="r in receipts || []" :key="r.execution_id" class="route-result">
      <b>{{ r.skill_id }} / {{ r.version }} · {{ r.status }}</b>
      <p>原节点 {{ r.node_id }} · 父步骤 {{ r.parent_id || 'SOP' }} · 工具 {{ r.observation?.tool_name || '未观察' }} · call {{ r.observation?.call_id || '未知' }}</p>
      <p v-if="r.error">停止：{{ r.error.code }} · {{ r.error.message }}</p>
      <RawDetails title="精确版本、可信输入来源与实际证据" :value="r" />
    </div>
  </section>
</template>
