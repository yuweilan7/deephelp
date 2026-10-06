<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { api } from "../../api/client";
import type {
  Page,
  SessionSummary,
  HistoryMessage,
  Question,
  MessageInput,
  RunResponse,
} from "../../api/types";
import { workspace } from "../../composables/workspace";
import { newMessage, uncertain } from "../../composables/message";
import { LatestRead } from "../../composables/latest";
import PageGuide from "../../components/PageGuide.vue";
import StateNotice from "../../components/StateNotice.vue";
import RunInspector from "./RunInspector.vue";
const sessions = ref<SessionSummary[]>([]),
  messages = ref<HistoryMessage[]>([]),
  search = ref(""),
  text = ref("");
const collaboration = ref(false);
const sessionNext = ref<number | null>(null),
  messageNext = ref<number | null>(null);
const hint = ref<Question | null>(null),
  incomplete = ref<HistoryMessage | null>(null),
  error = ref(""),
  loading = ref(false),
  last = ref<MessageInput | null>(null),
  result = ref<RunResponse | null>(null);
const historyRead = new LatestRead(),
  sessionRead = new LatestRead();
const filtered = computed(() =>
  sessions.value.filter((s) => s.session_id.includes(search.value)),
);
async function loadSessions(offset = 0) {
  try {
    await sessionRead.run(
      (signal) =>
        api.request<Page<SessionSummary>>(
          `/history/sessions?offset=${offset}`,
          "GET",
          undefined,
          signal,
        ),
      (p) => {
        sessions.value = offset ? [...sessions.value, ...p.items] : p.items;
        sessionNext.value = p.next_offset;
      },
    );
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function loadMessages(offset = 0) {
  loading.value = true;
  error.value = "";
  const sid = workspace.session;
  try {
    await historyRead.run(
      (signal) =>
        api.request<Page<HistoryMessage>>(
          `/history/sessions/${encodeURIComponent(sid)}/messages?offset=${offset}`,
          "GET",
          undefined,
          signal,
        ),
      (p) => {
        messages.value = offset ? [...messages.value, ...p.items] : p.items;
        messageNext.value = p.next_offset;
        loading.value = false;
      },
    );
  } catch (e) {
    error.value = (e as Error).message;
    loading.value = false;
  }
}
function selectSession(id?: string) {
  if (workspace.pending || workspace.sending) {
    error.value = "原消息结果待确认，请先核对 / 重试原消息。";
    return;
  }
  historyRead.cancel();
  workspace.session = id || crypto.randomUUID();
  workspace.selected = null;
  incomplete.value = null;
  messages.value = [];
  hint.value = null;
  last.value = null;
  result.value = null;
  error.value = "";
  messageNext.value = null;
  if (id) void loadMessages();
  else loading.value = false;
}
async function send(payload: MessageInput) {
  if (workspace.sending) return;
  if (
    workspace.pending &&
    workspace.pending.message_id !== payload.message_id
  ) {
    error.value = "先处理待确认的原消息。";
    return;
  }
  workspace.pending = payload;
  workspace.sending = true;
  error.value = "";
  try {
    const r = await api.request<RunResponse>("/converse", "POST", payload);
    workspace.pending = null;
    last.value = payload;
    result.value = r;
    incomplete.value = null;
    text.value = "";
    hint.value = null;
    workspace.selected = { session_id: payload.session_id, response: r };
    await loadMessages();
    await loadSessions();
    await api.connect();
  } catch (e) {
    if (!uncertain(e)) workspace.pending = null;
    error.value =
      (e as Error).message +
      (workspace.pending
        ? " 原消息正文、时间和编号已保留；明确核对 / 重试会沿用它们。浏览器停止等待不代表服务器取消。"
        : "");
  } finally {
    workspace.sending = false;
  }
}
function submit() {
  if (text.value.trim() && !workspace.pending)
    void send({
      ...newMessage(workspace.session, text.value, hint.value),
      execution_mode: collaboration.value ? "collaboration" : "single",
      ...(workspace.historyMemory?.session === workspace.session
        ? { history_memory: workspace.historyMemory.use }
        : {}),
    });
}
function selectRun(message: HistoryMessage) {
  incomplete.value = message.response ? null : message;
  workspace.selected = message.response
    ? { session_id: workspace.session, response: message.response }
    : null;
}
onMounted(async () => {
  await loadSessions();
  if (workspace.pending) {
    workspace.session = workspace.pending.session_id;
    error.value = "上次消息结果待确认，先核对 / 重试原消息。";
  }
});
onBeforeUnmount(() => {
  historyRead.cancel();
  sessionRead.cancel();
});
</script>
<template>
  <PageGuide
    title="对话与全过程"
    goal="让回答关联到真实持久事实，沿同一个问题补充，再阅读原运行证据。"
    needs="个人会话、业务资料和服务端明确启用的执行模式"
    :steps="[
      '准备一条个人订单',
      '提问：我的订单没有享受优惠',
      '点击补充此问题，再填写订单号',
      '选中事实 / 时间线并核对原消息重投',
    ]"
    expected="缺槽等待补充；完成后出现原事实与证据。重投读取原 receipt，本次调用计数独立显示。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/08-message-path.md"
  />
  <div class="conversation-grid">
    <aside class="card">
      <div class="section-title">
        <h2>持久会话</h2>
        <el-button @click="selectSession()">新会话</el-button>
      </div>
      <el-input
        v-model="search"
        aria-label="检索已读取会话"
        placeholder="检索会话编号"
      /><el-button @click="loadSessions()">刷新历史 · 0 模型</el-button
      ><button
        v-for="s in filtered"
        :key="s.session_id"
        class="session-row"
        :aria-current="workspace.session === s.session_id"
        @click="selectSession(s.session_id)"
      >
        <span>{{ s.session_id }}</span
        ><small
          >{{ s.message_count }} 条 ·
          {{ new Date(s.updated_at).toLocaleString() }}</small
        >
      </button>
      <p v-if="!filtered.length">
        没有匹配的会话。可新建会话或继续读取下一页。
      </p>
      <el-button v-if="sessionNext !== null" @click="loadSessions(sessionNext!)"
        >更多会话</el-button
      >
    </aside>
    <section class="card chat">
      <h2>当前会话</h2>
      <el-checkbox
        v-model="collaboration"
        :disabled="
          !workspace.connection?.execution.collaboration_available ||
          workspace.sending ||
          !!workspace.pending
        "
      >
        显式选择专业 Agent 协作
      </el-checkbox>
      <p>
        默认沿原单 Agent
        路径。协作会增加委托与证据回收模型步骤，共享本次预算；缺槽先补充，浏览历史仍为
        0 模型。
      </p>
      <p class="identifier">{{ workspace.session }}</p>
      <StateNotice :error="error" :loading="loading" />
      <article v-for="m in messages" :key="m.input.message_id" class="turn">
        <p class="user-text">{{ m.input.raw_text }}</p>
        <p>
          {{
            m.response?.reply ||
            `执行状态 ${m.status}；未完成结果不会自动重领。`
          }}
        </p>
        <small
          >{{ m.response?.outcome || m.status }} · run {{ m.run_id }}</small
        >
        <div class="actions">
          <el-button @click="selectRun(m)">事实 / 时间线</el-button
          ><el-button
            v-if="m.response"
            :disabled="!!workspace.pending || workspace.sending"
            @click="send(m.input)"
            >重投此消息</el-button
          ><el-button
            v-if="['ACTIVE', 'WAITING_SLOT'].includes(m.question.status)"
            :disabled="!!workspace.pending || workspace.sending"
            @click="hint = m.question"
            >补充此问题</el-button
          >
        </div>
      </article>
      <p v-if="!messages.length && !loading">
        这里还没有消息。执行后从持久账本读取结果。
      </p>
      <el-button v-if="messageNext !== null" @click="loadMessages(messageNext!)"
        >继续读取消息</el-button
      >
      <form @submit.prevent="submit">
        <p v-if="workspace.historyMemory?.session === workspace.session">
          当前新消息已选择
          {{
            workspace.historyMemory.use.references.length
          }}条历史精确版本。实际run将再次检查来源、授权与当前问题；可在“多轮与记忆”取消选择。
        </p>
        <p>
          {{
            hint
              ? `补充问题 ${hint.question_id} · 版本 ${hint.version}`
              : "正在提出新问题"
          }}
        </p>
        <el-button v-if="hint" @click="hint = null">改为新问题</el-button
        ><label for="message-text">你的问题或补充</label
        ><el-input
          id="message-text"
          v-model="text"
          type="textarea"
          :rows="4"
          :maxlength="16000"
          :disabled="!!workspace.pending || workspace.sending"
          placeholder="例如：我的订单没有享受优惠，请帮我核对。"
        />
        <p v-if="workspace.connection" class="muted">
          模式 {{ workspace.connection.execution.mode }} · 单条预计
          {{ workspace.connection.execution.expected_calls_per_message }} 次 ·
          剩余 {{ workspace.connection.execution.remaining_calls }} 次预约 ·
          已计 {{ workspace.connection.execution.charged_tokens }} / 上限
          {{ workspace.connection.execution.max_tokens }} tokens · 超时
          {{ workspace.connection.execution.request_timeout_seconds }} 秒。
        </p>
        <el-button
          native-type="submit"
          type="primary"
          :disabled="
            !workspace.connection ||
            !!workspace.pending ||
            workspace.sending ||
            !text.trim()
          "
          :loading="workspace.sending"
          >明确执行对话</el-button
        ><el-button
          v-if="workspace.pending"
          :disabled="workspace.sending"
          @click="send(workspace.pending!)"
          >核对 / 重试原消息</el-button
        ><el-button
          v-if="last && !workspace.pending"
          :disabled="workspace.sending"
          @click="send(last!)"
          >核对原消息重投</el-button
        >
      </form>
      <div v-if="result" class="result" role="status">
        <b
          >本次返回 {{ result.outcome }} ·
          {{ result.replayed ? "原 receipt 重放" : "新执行" }}</b
        >
        <p>{{ result.reply }}</p>
        <p>
          本次模型 {{ result.call_counts.model_calls }} / 检索
          {{ result.call_counts.retrieval_calls }} / 工具
          {{ result.call_counts.tool_calls }}
        </p>
      </div>
    </section>
    <RunInspector :incomplete="incomplete" />
  </div>
</template>
