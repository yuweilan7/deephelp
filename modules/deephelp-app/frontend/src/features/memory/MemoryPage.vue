<script setup lang="ts">
import { ref, watch } from "vue";
import { workspace } from "../../composables/workspace";
import { useRead, useAction } from "../../composables/requests";
import { api } from "../../api/client";
import type { LongMemoryCandidates } from "../../api/types";
import type { MemoryResult } from "../../api/learning";
import PageGuide from "../../components/PageGuide.vue";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
import MechanismGuide from "../../components/MechanismGuide.vue";
import { explanationPath, type Explanation } from "../../api/explanation";
const session = ref(workspace.selected?.session_id || workspace.session),
  memory = useRead<MemoryResult>(),
  turns = useRead<Explanation>();
const targetSession = ref(workspace.session),
  sourceSession = ref(session.value),
  sourceQuestion = ref(workspace.selected?.response.question_id || ""),
  sourceSessions = ref(""),
  selectedMemories = ref<string[]>([]),
  history = useRead<LongMemoryCandidates>(),
  memoryDetail = useRead<unknown>(),
  action = useAction(),
  correction = ref("");
async function changeMemory(
  memoryId: string,
  revision: number,
  change: "retain" | "correct" | "revoke" | "forget",
) {
  await api.request(
    "/learning/long-memory/records/" + encodeURIComponent(memoryId),
    "PUT",
    {
      expected_revision: revision,
      action: change,
      edit_id: crypto.randomUUID(),
      ...(change === "retain" || change === "correct"
        ? { text: correction.value }
        : {}),
    },
  );
  await loadHistory();
  action.notice.value =
    "记忆版本已更新。遗忘关闭后续检索；原业务账本和旧回执仍按审计规则保留。";
}
async function loadHistory() {
  memoryDetail.clear();
  selectedMemories.value = [];
  workspace.historyMemory = null;
  await history.load(
    "/learning/long-memory/" + encodeURIComponent(targetSession.value),
  );
  sourceSessions.value =
    history.data.value?.scope.source_sessions.join(",") || "";
}
async function preserveHistory() {
  await api.request("/learning/long-memory/preserve", "POST", {
    source_session: sourceSession.value,
    source_question: sourceQuestion.value,
  });
  action.notice.value = "已保留当前合法来源摘要。允许来源后读取候选。";
}
async function configureHistory(enabled: boolean) {
  const revision = history.data.value?.scope.revision;
  if (revision === undefined) throw new Error("请先读取目标会话的授权范围。");
  await api.request(
    "/learning/long-memory/" +
      encodeURIComponent(targetSession.value) +
      "/scope",
    "PUT",
    {
      expected_revision: revision,
      enabled,
      source_sessions: sourceSessions.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    },
  );
  await loadHistory();
}
function chooseHistory() {
  const data = history.data.value;
  if (!data || targetSession.value !== workspace.session) {
    action.error.value = "选择仅绑定当前对话会话；先在对话页切到目标会话。";
    return;
  }
  const references = data.candidates
    .filter(
      (c) => c.memory && selectedMemories.value.includes(c.reference.memory_id),
    )
    .map((c) => c.reference);
  if (references.length > 4) {
    action.error.value = "一次最多选择4条历史。";
    return;
  }
  workspace.historyMemory = references.length
    ? {
        session: targetSession.value,
        use: { scope_revision: data.scope.revision, references },
      }
    : null;
  action.notice.value = references.length
    ? "下次新消息将使用所选精确版本；原消息重投保持原选择。"
    : "已取消下次新消息的历史选择。";
}
watch(targetSession, () => {
  memoryDetail.clear();
  history.clear();
  selectedMemories.value = [];
  workspace.historyMemory = null;
});
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
      <h2>跨会话历史 · 明确保留与选择</h2>
      <p>
        摘要是派生上下文。当前订单、金额和券状态仍查询原业务事实；历史不会确认当前槽位、重开旧问题或批准旧操作。所有保留、设置和浏览为0模型。
      </p>
      <label>来源会话<input v-model="sourceSession" /></label>
      <label>已结束来源问题<input v-model="sourceQuestion" /></label>
      <el-button
        :disabled="action.busy.value"
        @click="action.run(preserveHistory)"
        >保留合法来源摘要</el-button
      >
      <label>目标会话<input v-model="targetSession" /></label>
      <el-button @click="loadHistory">读取授权与候选 · 0模型</el-button>
      <label
        >允许的来源会话（逗号分隔，最多8个）<input v-model="sourceSessions"
      /></label>
      <el-button
        :disabled="action.busy.value || !history.data.value"
        @click="action.run(() => configureHistory(true))"
        >明确开启此来源范围</el-button
      >
      <el-button
        :disabled="action.busy.value || !history.data.value"
        @click="action.run(() => configureHistory(false))"
        >停用跨会话使用</el-button
      >
      <StateNotice
        :error="action.error.value || history.error.value"
        :loading="action.busy.value || history.loading.value"
      />
      <p>{{ action.notice.value }}</p>
      <template v-if="history.data.value">
        <label
          >明确保留或更正的稳定信息<input v-model="correction" maxlength="2000"
        /></label>
        <p>
          业务摘要须与当前已确认订单和意图一致。用户保留文字只作为明确来源的个人陈述；更正不能改写原订单事实。撤回与遗忘不能重新开启。
        </p>
        <p>
          授权版本 {{ history.data.value.scope.revision }} ·
          {{ history.data.value.scope.enabled ? "已开启" : "未开启" }} · 候选
          {{
            history.data.value.candidates.length
          }}条。候选合法不等于原run已采用。
        </p>
        <div
          v-for="c in history.data.value.candidates"
          :key="c.reference.memory_id"
        >
          <label v-if="c.memory"
            ><input
              v-model="selectedMemories"
              type="checkbox"
              :value="c.reference.memory_id"
            />候选版本 {{ c.reference.revision }} · 来源
            {{ c.memory.source_session }} / {{ c.memory.source_question }} /
            源版本 {{ c.memory.source_version }}</label
          >
          <p>{{ c.reason }} · {{ c.memory?.text || "拒绝，未返回内容" }}</p>
          <el-button
            @click="
              memoryDetail.load(
                '/learning/long-memory/records/' +
                  encodeURIComponent(c.reference.memory_id),
              )
            "
            >读取版本审计 · 0模型</el-button
          >
          <el-button
            v-if="c.memory"
            :disabled="action.busy.value || !correction"
            @click="
              action.run(() =>
                changeMemory(
                  c.reference.memory_id,
                  c.reference.revision,
                  c.memory?.kind === 'user_retained' ? 'correct' : 'retain',
                ),
              )
            "
            >{{
              c.memory.kind === "user_retained" ? "明确更正" : "保留用户陈述"
            }}</el-button
          >
          <el-button
            v-if="c.memory"
            :disabled="action.busy.value"
            @click="
              action.run(() =>
                changeMemory(
                  c.reference.memory_id,
                  c.reference.revision,
                  'revoke',
                ),
              )
            "
            >撤回此条</el-button
          >
          <el-button
            :disabled="action.busy.value || c.reason === 'memory_forgotten'"
            @click="
              action.run(() =>
                changeMemory(
                  c.reference.memory_id,
                  c.reference.revision,
                  'forget',
                ),
              )
            "
            >遗忘此条</el-button
          >
          <p v-if="c.memory">
            保留时间 {{ c.memory.created_at }} · 有效至
            {{ c.memory.expires_at }} · 原消息
            {{ c.memory.source_messages.join(", ") }} · 证据
            {{ c.memory.evidence_ids.join(", ") || "原问题未带工具证据" }}
          </p>
        </div>
        <el-button @click="chooseHistory"
          >将选择绑定下次当前会话新消息</el-button
        >
        <RawDetails :value="history.data.value" />
        <StateNotice
          :error="memoryDetail.error.value"
          :loading="memoryDetail.loading.value"
        />
        <RawDetails
          v-if="memoryDetail.data.value"
          :value="memoryDetail.data.value"
        />
      </template>
      <template v-if="workspace.selected?.response.memory_selection">
        <h3>所选原run的历史选择</h3>
        <p>
          {{ workspace.selected.response.memory_selection.status }} · 策略
          {{ workspace.selected.response.memory_selection.strategy }} ·
          {{ workspace.selected.response.memory_selection.context_bytes }}字节
        </p>
        <p
          v-for="c in workspace.selected.response.memory_selection.decisions"
          :key="c.reference.memory_id"
        >
          {{ c.status }} · {{ c.reason }} ·
          {{ c.memory?.source_session || "无合法内容" }}
        </p>
        <RawDetails :value="workspace.selected.response.memory_selection" />
      </template>
    </section>
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
