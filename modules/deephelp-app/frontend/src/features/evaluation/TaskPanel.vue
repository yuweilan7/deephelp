<script setup lang="ts">
import { ref, watch, onMounted } from "vue";
import { api } from "../../api/client";
import {
  lines,
  canStart,
  canCancel,
  canResume,
  type Catalog,
  type Plan,
  type Task,
} from "../../api/evaluation";
import { workspace } from "../../composables/workspace";
import { useRead, useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const props = defineProps<{
    catalog: Catalog;
    plan: Plan | null;
    id: string;
  }>(),
  emit = defineEmits<{ "update:id": [id: string]; refresh: [] }>();
const mode = ref("audit"),
  ids = ref(""),
  assetId = ref(""),
  confirmed = ref(false),
  task = useRead<Task>(),
  asset = useRead<{
    version: string;
    status: string;
    files_verified: boolean;
    reviews_current: boolean;
    note: string;
  }>(),
  action = useAction();
const skill = ref("");
const skills = useRead<{
  candidates: { candidate_digest: string; status: string }[];
}>();
onMounted(() => skills.load("/learning/skills"));
watch(
  () => props.catalog.assets,
  (rows) => {
    if (!rows.includes(assetId.value)) assetId.value = rows[0] || "";
  },
  { immediate: true },
);
watch(
  () => props.id,
  () => task.clear(),
);
watch(assetId, () => asset.clear());
watch(mode, () => {
  confirmed.value = false;
});
function read() {
  if (props.id)
    void task.load(
      "/learning/evaluation/tasks/" + encodeURIComponent(props.id),
    );
}
function start() {
  if (!confirmed.value || !canStart(props.catalog.tasks)) return;
  void action.run(async () => {
    const t = await api.request<Task>("/learning/evaluation/tasks", "POST", {
      action: mode.value,
      candidate_ids: mode.value === "build" ? lines(ids.value) : [],
      asset_id: ["validate", "prepare"].includes(mode.value)
        ? assetId.value
        : null,
      skill_candidate: ["validate", "prepare"].includes(mode.value)
        ? skill.value || null
        : null,
      confirmed: true,
    });
    confirmed.value = false;
    emit("update:id", t.id);
    emit("refresh");
  });
}
function control(op: "cancel" | "resume") {
  if (
    op === "cancel"
      ? !canCancel(task.data.value)
      : !canResume(task.data.value, props.plan?.max_task_attempts || 3)
  )
    return;
  void action.run(async () => {
    await api.request(
      `/learning/evaluation/tasks/${encodeURIComponent(props.id)}/${op}`,
      "POST",
    );
    await task.load(
      "/learning/evaluation/tasks/" + encodeURIComponent(props.id),
    );
    emit("refresh");
  });
}
</script>
<template>
  <section class="card">
    <h2>显式任务 · 同时一个</h2>
    <template v-if="plan"
      ><p>
        离线：{{ plan.offline.conversations }} 会话 / 每路
        {{ plan.offline.messages }} 消息 × {{ plan.offline.modes }} 路，0
        远程模型。
      </p>
      <p>
        构建：基线 {{ plan.build.base_corpus_rows }} 条，最多
        {{ plan.build.max_candidates }} 个已批准来源；预计 Embedding
        {{ plan.build.embedding_calls_before_retry.join("–") }} 次，输入约
        {{ plan.build.embedding_input_tokens_estimate.join("–") }}
        tokens。可选训练走原门禁。
      </p>
      <p>
        验证：{{ plan.validate.conversations }} 会话 /
        {{ plan.validate.messages }} 消息，预计远程
        {{ plan.validate.remote_calls_estimate.join("–") }} 次；每条输入
        {{ plan.validate.per_message_tokens_estimate.input.join("–") }} / 输出
        {{ plan.validate.per_message_tokens_estimate.output.join("–") }}
        tokens。推理沿原配置与实际 usage，未知项保持未知；估算不等于实测。
      </p>
      <p>
        总超时 {{ plan.timeout_seconds }} 秒 · 最多
        {{ plan.max_task_attempts }}
        次尝试；恢复另建不可变目录，累计预算不重置。
      </p></template
    >
    <p>
      共享剩余
      {{ workspace.connection?.execution.remaining_calls ?? "未知" }} 次预约 ·
      已计 {{ workspace.connection?.execution.charged_tokens ?? "未知" }} / 上限
      {{ workspace.connection?.execution.max_tokens ?? "未知" }} tokens。
    </p>
    <form @submit.prevent="start">
      <label for="task-action">任务模式</label
      ><select id="task-action" v-model="mode">
        <option value="audit">冻结集审计 · 0 模型</option>
        <option value="offline">四路离线回归 · 0 模型</option>
        <option value="build">
          构建已审核候选 · 真实 Embedding / 可选训练
        </option>
        <option value="validate">候选内容验证 · 真实模型</option>
        <option value="prepare">
          准备 / 登记完整版本 · 原精确门禁
        </option></select
      ><label for="build-ids">构建候选编号（每行一条；空白构建基线）</label
      ><textarea id="build-ids" v-model="ids" /><label for="task-asset"
        >候选资产</label
      ><select id="task-asset" v-model="assetId">
        <option v-for="a in catalog.assets" :key="a">{{ a }}</option></select
      ><el-button
        :disabled="!assetId"
        @click="
          asset.load(
            '/learning/evaluation/assets/' + encodeURIComponent(assetId),
          )
        "
        >核对资产与当前审核</el-button
      ><StateNotice :error="asset.error.value" :loading="asset.loading.value" />
      <p v-if="asset.data.value">
        {{ asset.data.value.version }} · {{ asset.data.value.status }} ·
        文件核对 {{ asset.data.value.files_verified }} · 审核当前
        {{ asset.data.value.reviews_current }} · {{ asset.data.value.note }}
      </p>
      <RawDetails v-if="asset.data.value" :value="asset.data.value" />
      <template v-if="['validate', 'prepare'].includes(mode)">
        <label>已审核Skill候选</label>
        <select v-model="skill">
          <option value="">未选择 · 新Skill发布必须选择已审核候选</option>
          <option
            v-for="s in skills.data.value?.candidates.filter(
              (c) => c.status === 'APPROVED',
            ) || []"
            :key="s.candidate_digest"
            :value="s.candidate_digest"
          >
            {{ s.candidate_digest }}
          </option>
        </select>
        <el-button @click="skills.load('/learning/skills')"
          >刷新Skill审核 · 0模型</el-button
        >
        <StateNotice
          :error="skills.error.value"
          :loading="skills.loading.value"
        />
        <p>
          验证与准备必须选择同一审核候选和资产。准备只登记完整版本；激活沿原发布CAS，不热替换当前会话。
        </p>
      </template>
      <el-checkbox v-model="confirmed"
        >已核对模式、样本与共享预算，明确启动</el-checkbox
      ><el-button
        native-type="submit"
        type="primary"
        :disabled="
          !confirmed ||
          !canStart(catalog.tasks) ||
          (['validate', 'prepare'].includes(mode) && !assetId)
        "
        :loading="action.busy.value"
        >启动选定任务</el-button
      >
    </form>
    <p v-if="!canStart(catalog.tasks)">
      已有运行 / 取消中的任务，请读取状态，等待结束或明确取消。
    </p>
    <label for="task-id">持久任务</label
    ><select
      id="task-id"
      :value="id"
      @change="emit('update:id', ($event.target as HTMLSelectElement).value)"
    >
      <option value="">选择任务</option>
      <option v-for="t in catalog.tasks" :key="t.id" :value="t.id">
        {{ t.options.action }} · {{ t.state }} · 尝试 {{ t.attempt }} ·
        {{ t.id.slice(0, 8) }}
      </option></select
    ><el-button :disabled="!id" @click="read">读取实际任务状态</el-button
    ><el-button
      :disabled="!canCancel(task.data.value) || action.busy.value"
      @click="control('cancel')"
      >取消任务</el-button
    ><el-button
      :disabled="
        !canResume(task.data.value, plan?.max_task_attempts || 3) ||
        action.busy.value
      "
      @click="control('resume')"
      >显式恢复任务</el-button
    ><StateNotice
      :error="task.error.value || action.error.value"
      :loading="task.loading.value"
    />
    <p v-if="task.data.value">
      任务 {{ task.data.value.state }} · 尝试 {{ task.data.value.attempt }} /
      {{ plan?.max_task_attempts ?? "未知" }} · 内容
      {{
        task.data.value.result?.evidence_status ||
        task.data.value.partial_evidence?.summary?.status ||
        "未有完整证据"
      }}
      · {{ task.data.value.error || "无任务错误" }}
    </p>
    <RawDetails
      v-if="task.data.value"
      title="任务 / 部分证据 / 尝试记录"
      :value="task.data.value"
    />
    <p>
      取消请求和实际 CANCELLED 分开；进程中断为
      INTERRUPTED。未知启动结果先刷新目录核对，不能自动重发。
    </p>
  </section>
</template>
