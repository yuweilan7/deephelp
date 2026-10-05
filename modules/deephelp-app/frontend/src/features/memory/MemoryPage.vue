<script setup lang="ts">
import { ref, watch } from "vue";
import { workspace } from "../../composables/workspace";
import { useRead } from "../../composables/requests";
import type { MemoryResult } from "../../api/learning";
import PageGuide from "../../components/PageGuide.vue";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
import MechanismGuide from "../../components/MechanismGuide.vue";
import { explanationPath, type Explanation } from "../../api/explanation";
const session = ref(workspace.selected?.session_id || workspace.session),
  memory = useRead<MemoryResult>(),
  turns = useRead<Explanation>();
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
    void turns.load(explanationPath(s.response.run_id, s.session_id));
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
      <MechanismGuide mechanism="memory" />
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
      <template v-if="memory.data.value">
        <p>
          这里是本次读取的当前快照，可能晚于所选run。Redis
          {{ memory.data.value.redis.status }} · TTL
          {{ memory.data.value.redis.ttl_seconds ?? "未知" }}秒 · 与PG一致
          {{
            memory.data.value.redis.matches_authority ?? "未知"
          }}。缺缓存不等于丢业务事实；读取不会重建投影。
        </p>
        <p v-for="o in memory.data.value.outbox" :key="o.event_id">
          outbox {{ o.event_id }} → 问题 {{ o.question_id }} / 版本
          {{ o.version }} · {{ o.done ? "worker已记录完成" : "仍待投影" }} ·
          尝试 {{ o.attempts }} · {{ o.last_error ?? "无记录错误" }}
        </p>
        <p>
          outbox完成是worker记录，未在浏览时执行Milvus相似检索或验证每条向量；checkpoint记录图进度，Langfuse记录诊断，均不能替代PG账本。
        </p>
      </template>
      <p v-else>未读取。先选择或填写一个自己的持久会话。</p>
    </section>
    <section class="card">
      <h2>原运行问题归属</h2>
      <p>run {{ workspace.selected?.response.run_id || "未选择" }}</p>
      <el-button :disabled="!workspace.selected" @click="readTurns"
        >读取同次运行多轮归属</el-button
      ><StateNotice :error="turns.error.value" :loading="turns.loading.value" />
      <template v-if="turns.data.value"
        ><p>原诉求：{{ turns.data.value.input.raw_text }}</p>
        <p>
          原问题
          {{ turns.data.value.question_at_run?.question_id ?? "诊断缺失" }} /
          版本
          {{ turns.data.value.question_at_run?.version ?? "未知" }}；当前版本
          {{ turns.data.value.question_now.version }}。
        </p>
        <p>
          归属
          {{
            turns.data.value.event?.judgement?.relation ??
            turns.data.value.event?.disposition ??
            "未记录"
          }}；未确定时不能替用户选事件。
        </p>
        <p v-for="(e, i) in turns.data.value.event?.edges" :key="i">
          关联边 {{ e.accepted ? "采用" : "拒绝" }} · 已记录原因 {{ e.reason }}
        </p>
        <p v-for="c in turns.data.value.event?.candidates" :key="c.question_id">
          候选问题 {{ c.question_id }} ·
          {{ c.excluded_reason ?? "未排除，是否采用看最终归属" }}
        </p>
        <p
          v-for="(c, i) in turns.data.value.event?.judgement?.citations"
          :key="i"
        >
          原引用 {{ c.message_id }}：“{{ c.quote }}”
        </p>
        <RawDetails
          title="归属候选、实体来源与排除记录"
          :value="turns.data.value"
      /></template>
    </section>
  </div>
</template>
