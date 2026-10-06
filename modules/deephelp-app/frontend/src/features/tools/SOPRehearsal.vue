<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../api/client";
import { useAction, useRead } from "../../composables/requests";
import StateNotice from "../../components/StateNotice.vue";
import RawDetails from "../../components/RawDetails.vue";
const props = defineProps<{
  draft: {
    draft_id: string;
    revision: number;
    content_hash: string;
    content: { required_slots: string[] };
    validation: { valid: boolean };
  };
  dirty: boolean;
  liveAvailable: boolean;
}>();
type Observation = {
  mode: string;
  status?: string;
  model_calls: number;
  business_writes: number;
  candidate_hash: string;
  fact_source: string;
  run_id: string;
  unexecuted_nodes: string[];
  timeline: {
    node_id: string;
    kind: string;
    status: string;
    next_node?: string;
    error_code?: string;
    matched_route?: number | null;
    condition?: unknown;
  }[];
  definition: {
    nodes: {
      node_id: string;
      kind: string;
      routes: unknown[];
      default?: string;
    }[];
  };
};
type Job = {
  id: string;
  state: string;
  options: { revision: number; draft_hash: string };
  result?: Observation;
  observation?: Observation;
  error?: string;
  diagnostic?: unknown;
};
const mode = ref("offline"),
  scenario = ref("normal"),
  confirmed = ref(false);
const slots = ref<Record<string, string>>({}),
  job = ref<Job | null>(null),
  records = ref<Job[]>([]),
  action = useAction();
const history = useRead<{ jobs: Job[] }>();
const defaults: Record<string, string> = {
  order_id: "90234105",
  coupon_id: "90222004",
  activity_id: "ACT-43",
  attempt_id: "TRY-43",
  refund_id: "REF-43",
};
watch(
  () => props.draft.draft_id,
  () => {
    slots.value = Object.fromEntries(
      props.draft.content.required_slots.map((s) => [s, defaults[s] || ""]),
    );
    mode.value = "offline";
    confirmed.value = false;
    const identity = props.draft.draft_id;
    records.value = [];
    job.value = null;
    void history
      .load(`/learning/sop/drafts/${identity}/rehearsals`)
      .then(() => {
        if (identity === props.draft.draft_id && history.data.value) {
          records.value = history.data.value.jobs;
          job.value = records.value[0] || null;
        }
      });
  },
  { immediate: true },
);
function start() {
  void action.run(async () => {
    job.value = await api.request<Job>(
      `/learning/sop/drafts/${props.draft.draft_id}/rehearsals`,
      "POST",
      {
        revision: props.draft.revision,
        mode: mode.value,
        scenario: mode.value === "offline" ? scenario.value : "normal",
        slots: Object.fromEntries(
          Object.entries(slots.value).filter(([, v]) => v.trim() !== ""),
        ),
        confirmed: confirmed.value,
      },
    );
    records.value = [job.value, ...records.value];
  });
}
function read(cancel = false) {
  void action.run(async () => {
    job.value = await api.request<Job>(
      `/learning/sop/rehearsals/${job.value!.id}${cancel ? "/cancel" : ""}`,
      cancel ? "POST" : "GET",
    );
    records.value = records.value.map((r) =>
      r.id === job.value!.id ? job.value! : r,
    );
  });
}
</script>
<template>
  <section class="card">
    <h4>SOP 受控预演</h4>
    <p>
      预演当前已保存的候选。默认使用固定合成事实与原执行器，模型0次、业务写入0次；结构通过和离线结果仍需正式内容与发布门禁。
    </p>
    <p>
      槽位由你明确确认，主体来自当前工作区；留空观察缺槽等待。预演建立独立问题，不修改原对话或审批。
    </p>
    <p>
      含审批提案的流程会在任何调用前拒绝预演；需要沿原正式审批边界另行验证。
    </p>
    <label v-for="slot in draft.content.required_slots" :key="slot"
      >预演 {{ slot }}<input v-model="slots[slot]"
    /></label>
    <label
      >预演模式<select v-model="mode">
        <option value="offline">离线固定动作 · 0 模型</option>
        <option value="live-readonly" :disabled="!liveAvailable">
          明确真实模型与只读 MCP
        </option>
      </select></label
    >
    <label v-if="mode === 'offline'"
      >合成情境<select v-model="scenario">
        <option value="normal">正常固定事实</option>
        <option value="invalid_fact">事实/证据编号不符</option>
        <option value="tool_failure">工具明确失败</option>
        <option value="slow">等待工具，供取消观察</option>
      </select></label
    >
    <template v-else>
      <p>
        使用当前模型、原身份与共享累计预算，仅查询。一次 lookup
        通常1次模型建议及1次只读工具，协议修正/有限重试会增加调用；取消可能已有派发，用原观察和预算核对。候选
        hash 在结果中保存。
      </p>
      <el-checkbox v-model="confirmed"
        >明确选择此次真实只读预演，确认输入并使用现有共享预算</el-checkbox
      >
    </template>
    <el-button
      :disabled="
        action.busy.value ||
        dirty ||
        !draft.validation.valid ||
        (mode === 'live-readonly' && !confirmed)
      "
      @click="start"
      >明确开始预演</el-button
    >
    <p v-if="dirty">有未保存修改，先保存并校验，避免展示另一份图的结果。</p>
    <label v-if="records.length"
      >持久预演记录 · 读取0模型<select
        :value="job?.id"
        @change="
          job =
            records.find(
              (r) => r.id === ($event.target as HTMLSelectElement).value,
            ) || null;
          read();
        "
      >
        <option v-for="r in records" :key="r.id" :value="r.id">
          revision {{ r.options.revision }} · {{ r.state }} · {{ r.id }}
        </option>
      </select></label
    >
    <template v-if="job">
      <p v-if="job.error">服务端记录：{{ job.error }}</p>
      <p>
        服务任务 {{ job.id }} · {{ job.state }} · 保存 revision
        {{ job.options.revision }}。终态来自服务端；等待页面不表示任务已停。
      </p>
      <p v-if="job.options.draft_hash !== draft.content_hash">
        此记录属于之前的候选，不能算作当前草稿的验收。
      </p>
      <el-button :disabled="action.busy.value" @click="read()"
        >读取原预演结果 · 0 模型</el-button
      >
      <el-button
        :disabled="
          action.busy.value || !['RUNNING', 'CANCELLING'].includes(job.state)
        "
        @click="read(true)"
        >取消并等待服务终态</el-button
      >
      <template
        v-for="o in [job.result || job.observation].filter(Boolean)"
        :key="o!.run_id"
      >
        <p>
          原预演 {{ o!.run_id }} · {{ o!.mode }} ·
          {{ o!.status || "结果尚未形成" }} · 模型 {{ o!.model_calls }} · 写入
          {{ o!.business_writes }}
        </p>
        <p>
          事实来源
          {{
            o!.fact_source === "fixed_synthetic" ? "固定合成观察" : "原只读 MCP"
          }}
          · 候选 {{ o!.candidate_hash }}
        </p>
        <ol>
          <li v-for="(n, i) in o!.timeline" :key="i">
            {{ n.node_id }} · {{ n.kind }} · {{ n.status }} →
            {{ n.next_node || "终点/未继续" }} {{ n.error_code || "" }}
            <template v-if="n.kind === 'branch'">
              ·
              {{
                n.matched_route == null
                  ? "未命中规则，沿默认连接"
                  : `实际命中第${n.matched_route + 1}条规则`
              }}
              {{ n.condition || "" }}</template
            >
          </li>
        </ol>
        <p>未运行节点：{{ o!.unexecuted_nodes.join("、") || "无" }}</p>
        <RawDetails
          title="该候选的有限分支条件与默认连接"
          :value="o!.definition.nodes.filter((n) => n.kind === 'branch')"
        />
      </template>
      <RawDetails title="原预演事实、节点时间、输入、版本和预算" :value="job" />
    </template>
    <StateNotice :error="action.error.value" :loading="action.busy.value" />
    <StateNotice
      :error="history.error.value"
      :loading="history.loading.value"
    />
  </section>
</template>
