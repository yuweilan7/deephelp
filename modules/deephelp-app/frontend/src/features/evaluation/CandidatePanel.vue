<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../api/client";
import { safeUrl } from "../../api/learning";
import {
  lines,
  canStart,
  type Catalog,
  type Candidate,
  type PlatformEvidence,
  type Task,
} from "../../api/evaluation";
import { useRead, useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const props = defineProps<{ catalog: Catalog }>(),
  emit = defineEmits<{ refresh: []; task: [id: string] }>();
const id = ref(""),
  source = ref(""),
  route = ref("corpus"),
  state = ref("disputed"),
  label = ref(""),
  phrases = ref(""),
  exclusions = ref(""),
  reason = ref(""),
  independent = ref(false),
  confirmed = ref(false),
  platformConfirm = ref(false),
  scoreId = ref("");
const candidate = useRead<{ candidate: Candidate }>(),
  platform = useRead<PlatformEvidence>(),
  action = useAction();
watch(
  () => props.catalog.candidates,
  (rows) => {
    if (!rows.some((r) => r.id === id.value)) id.value = rows[0]?.id || "";
  },
  { immediate: true },
);
watch(
  () => props.catalog.capture_sources,
  (ids) => {
    if (!ids.includes(source.value)) source.value = ids[0] || "";
  },
  { immediate: true },
);
function reset() {
  candidate.clear();
  platform.clear();
  confirmed.value = false;
  platformConfirm.value = false;
  scoreId.value = "";
  independent.value = false;
}
watch(id, reset);
async function read() {
  platform.clear();
  confirmed.value = false;
  platformConfirm.value = false;
  scoreId.value = "";
  independent.value = false;
  await candidate.load(
    "/learning/evaluation/candidates/" + encodeURIComponent(id.value),
  );
}
function capture() {
  if (!source.value) return;
  void action.run(async () => {
    const c = await api.request<Candidate>(
      "/learning/evaluation/capture",
      "POST",
      { run_id: source.value },
    );
    id.value = c.candidate_id;
    emit("refresh");
    action.notice.value = "来源已采集，尚未审核，不自动构建。";
  });
}
function review() {
  if (
    !confirmed.value ||
    !candidate.data.value ||
    candidate.data.value.candidate.candidate_id !== id.value
  )
    return;
  void action.run(async () => {
    const c = candidate.data.value!.candidate;
    await api.request(
      "/learning/evaluation/candidates/" +
        encodeURIComponent(c.candidate_id) +
        "/review",
      "POST",
      {
        revision: c.revision,
        platform_score_id: scoreId.value || null,
        route: route.value === "withdraw" ? null : route.value,
        review:
          route.value === "withdraw"
            ? null
            : {
                state: state.value,
                label: label.value || null,
                reviewer: "browser-owner",
                method: "human",
                reason: reason.value,
                phrases: lines(phrases.value),
                exclusions: lines(exclusions.value),
                independent_from_heldout: independent.value,
              },
        reason: reason.value,
        confirmed: true,
      },
    );
    await read();
    emit("refresh");
    action.notice.value = "审核已保存，构建仍需明确启动。";
  });
}
function exportCandidate() {
  if (
    !platformConfirm.value ||
    !candidate.data.value ||
    !canStart(props.catalog.tasks)
  )
    return;
  void action.run(async () => {
    const c = candidate.data.value!.candidate;
    const t = await api.request<Task>("/learning/evaluation/tasks", "POST", {
      action: "platform_candidate",
      candidate_ids: [c.candidate_id],
      candidate_revision: c.revision,
      confirmed: true,
    });
    platformConfirm.value = false;
    emit("task", t.id);
    emit("refresh");
  });
}
async function readPlatform() {
  const c = candidate.data.value?.candidate;
  if (!c) return;
  await platform.load(
    `/learning/evaluation/candidates/${encodeURIComponent(c.candidate_id)}/platform`,
  );
  if (platform.data.value && platform.data.value.revision !== c.revision) {
    platform.clear();
    action.error.value = "候选 revision 已变，请重新读取。";
  }
}
</script>
<template>
  <section class="card">
    <h2>独立采集与人工审核</h2>
    <p>只采集原 m18-collect 已完成来源，不把冻结留出集再用作训练。</p>
    <label for="capture-source">可采集的专用合成 run</label
    ><select id="capture-source" v-model="source">
      <option v-for="s in catalog.capture_sources" :key="s">
        {{ s }}
      </option></select
    ><el-button
      :disabled="!source"
      :loading="action.busy.value"
      @click="capture"
      >采集已有来源 · 0 模型</el-button
    ><label for="candidate-id">反馈候选</label
    ><select id="candidate-id" v-model="id">
      <option v-for="c in catalog.candidates" :key="c.id" :value="c.id">
        r{{ c.revision }} · {{ c.text.slice(0, 55) }}
      </option></select
    ><el-button :disabled="!id" @click="read">读取审核与派生</el-button
    ><StateNotice
      :error="candidate.error.value"
      :loading="candidate.loading.value"
    /><RawDetails
      v-if="candidate.data.value"
      title="核对候选内容、revision 与不可变派生"
      :value="candidate.data.value"
    />
    <p v-else>先读取当前候选；无候选时先准备独立采集来源。</p>
    <div v-if="catalog.platform_review_enabled">
      <h3>平台人工标注依据</h3>
      <el-checkbox v-model="platformConfirm" :disabled="!candidate.data.value"
        >确认导出当前 revision · 0 模型</el-checkbox
      ><el-button
        :disabled="
          !platformConfirm || !candidate.data.value || !canStart(catalog.tasks)
        "
        @click="exportCandidate"
        >导出当前候选到平台</el-button
      ><el-button :disabled="!candidate.data.value" @click="readPlatform"
        >读取当前 revision 平台标注</el-button
      ><StateNotice
        :error="platform.error.value"
        :loading="platform.loading.value"
      />
      <p v-if="platform.data.value">
        {{ platform.data.value.status }} · revision
        {{ platform.data.value.revision }} · 人工标注
        {{ platform.data.value.scores.length }}
      </p>
      <a
        v-if="safeUrl(platform.data.value?.url)"
        :href="safeUrl(platform.data.value?.url)"
        target="_blank"
        rel="noopener"
        >打开当前候选</a
      ><label for="platform-score">本次审核依据</label
      ><select id="platform-score" v-model="scoreId">
        <option value="">无平台依据，使用原独立审核</option>
        <option
          v-for="s in platform.data.value?.scores || []"
          :key="s.id"
          :value="s.id"
        >
          {{ s.stringValue }} · {{ s.id }} · {{ s.comment }}
        </option>
      </select>
      <p>平台分数只作为人工依据，仍需原 revision、独立标签及路由审核。</p>
    </div>
    <form @submit.prevent="review">
      <label for="review-route">审核路由</label
      ><select id="review-route" v-model="route">
        <option value="corpus">检索语料</option>
        <option value="fasttext">FastText</option>
        <option value="rule">规则</option>
        <option value="withdraw">撤回全部路由</option></select
      ><label for="review-state">结论</label
      ><select id="review-state" v-model="state">
        <option value="disputed">有争议</option>
        <option value="rejected">拒绝</option>
        <option value="approved">批准</option></select
      ><label for="review-label">独立标签</label
      ><select id="review-label" v-model="label">
        <option value="">尚未确认</option>
        <option value="DISCOUNT_MISSING">优惠未到账</option>
        <option value="COUPON_UNUSABLE">优惠券不可用</option>
        <option value="ORDER_ACTIVITY_QUERY">订单活动查询</option></select
      ><label for="review-phrases">规则短语（每行一条）</label
      ><textarea id="review-phrases" v-model="phrases" /><label
        for="review-exclusions"
        >规则排除短语（每行一条）</label
      ><textarea id="review-exclusions" v-model="exclusions" /><label
        for="review-reason"
        >依据 / 反例</label
      ><textarea
        id="review-reason"
        v-model="reason"
        required
        maxlength="1000"
      /><el-checkbox v-model="independent">已核对与冻结留出集独立</el-checkbox
      ><el-checkbox v-model="confirmed" :disabled="!candidate.data.value"
        >已核对内容及 revision，独立确认审核</el-checkbox
      ><el-button
        native-type="submit"
        :disabled="!confirmed || !candidate.data.value"
        :loading="action.busy.value"
        >保存人工审核</el-button
      >
    </form>
    <StateNotice :error="action.error.value" />
    <p role="status">{{ action.notice.value }}</p>
  </section>
</template>
