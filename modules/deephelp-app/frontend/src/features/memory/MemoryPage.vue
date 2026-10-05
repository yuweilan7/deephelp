<script setup lang="ts">
import { ref, watch } from "vue";
import { workspace } from "../../composables/workspace";
import { useRead } from "../../composables/requests";
import type { MemoryResult } from "../../api/learning";
import PageGuide from "../../components/PageGuide.vue";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const session = ref(workspace.selected?.session_id || workspace.session),
  memory = useRead<MemoryResult>(),
  turns = useRead<unknown>();
watch(
  () => workspace.selected,
  () => {
    session.value = workspace.selected?.session_id || workspace.session;
    memory.clear();
    turns.clear();
  },
);
watch(session, () => {
  memory.clear();
  turns.clear();
});
function readTurns() {
  const s = workspace.selected;
  if (s?.response.run_id)
    void turns.load(
      `/debug/runs/${encodeURIComponent(s.response.run_id)}/turns?session_id=${encodeURIComponent(s.session_id)}`,
    );
}
</script>
<template>
  <PageGuide
    title="多轮与记忆"
    goal="核对同一会话的问题归属、PG 权威快照和可重建投影。"
    needs="已有个人会话、PG；Redis 是可重建投影，读取失败不能伪造为空"
    :steps="[
      '在主对话交错两个问题',
      '选中原 run，或填写会话编号',
      '读取 PG / Redis / outbox 实际状态',
      '查看同次运行归属，确认补充没有串入另一问题',
    ]"
    expected="PG generation、Redis 状态和待投影 outbox 来自当前原记录；浏览新增模型调用 0。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/11-memory.md"
  />
  <div class="two-column">
    <section class="card">
      <form
        @submit.prevent="
          memory.load(
            '/learning/memory?session_id=' + encodeURIComponent(session),
          )
        "
      >
        <label for="memory-session">会话编号</label
        ><input id="memory-session" v-model="session" required /><el-button
          native-type="submit"
          >读取实际记忆 · 0 模型</el-button
        >
      </form>
      <StateNotice
        :error="memory.error.value"
        :loading="memory.loading.value"
      />
      <p v-if="memory.data.value">
        权威 {{ memory.data.value.authority }} · generation
        {{ memory.data.value.window.generation }} · Redis
        {{ memory.data.value.redis.status }} · outbox
        {{ memory.data.value.outbox.length }} 条
      </p>
      <RawDetails v-if="memory.data.value" :value="memory.data.value" />
      <p v-else>未读取。先选择或填写一个自己的持久会话。</p>
    </section>
    <section class="card">
      <h2>原运行问题归属</h2>
      <p>run {{ workspace.selected?.response.run_id || "未选择" }}</p>
      <el-button :disabled="!workspace.selected" @click="readTurns"
        >读取同次运行多轮归属</el-button
      ><StateNotice
        :error="turns.error.value"
        :loading="turns.loading.value"
      /><RawDetails v-if="turns.data.value" :value="turns.data.value" />
    </section>
  </div>
</template>
