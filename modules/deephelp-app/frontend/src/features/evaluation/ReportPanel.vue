<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../api/client";
import { safeUrl } from "../../api/learning";
import {
  canStart,
  type Catalog,
  type Report,
  type HistoricalImport,
  type Task,
} from "../../api/evaluation";
import { useRead, useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const props = defineProps<{ catalog: Catalog }>(),
  emit = defineEmits<{ refresh: []; task: [id: string] }>();
const reportId = ref(""),
  importId = ref(""),
  confirmed = ref(false),
  report = useRead<Report>(),
  mapping = useRead<HistoricalImport>(),
  action = useAction();
watch(
  () => props.catalog.reports,
  (ids) => {
    if (!ids.includes(reportId.value)) reportId.value = ids[0] || "";
  },
  { immediate: true },
);
watch(
  () => props.catalog.platform_imports,
  (ids) => {
    if (!ids.includes(importId.value)) importId.value = ids[0] || "";
  },
  { immediate: true },
);
watch(reportId, () => report.clear());
watch(importId, () => {
  mapping.clear();
  confirmed.value = false;
});
function readReport(offset = 0) {
  if (reportId.value)
    void report.load(
      `/learning/evaluation/reports/${encodeURIComponent(reportId.value)}?offset=${offset}`,
    );
}
function upload() {
  if (!confirmed.value || !importId.value || !canStart(props.catalog.tasks))
    return;
  void action.run(async () => {
    const t = await api.request<Task>("/learning/evaluation/tasks", "POST", {
      action: "platform_upload",
      import_id: importId.value,
      confirmed: true,
    });
    confirmed.value = false;
    emit("task", t.id);
    emit("refresh");
  });
}
</script>
<template>
  <section class="card">
    <h2>冻结报告与差例</h2>
    <label for="evaluation-report">受控历史报告</label
    ><select id="evaluation-report" v-model="reportId">
      <option v-for="id in catalog.reports" :key="id">{{ id }}</option></select
    ><el-button :disabled="!reportId" @click="readReport()"
      >读取指标与差例 · 0 模型</el-button
    ><StateNotice :error="report.error.value" :loading="report.loading.value" />
    <p v-if="!catalog.reports.length">
      无受控报告，请先配置目录或明确运行离线评测任务。
    </p>
    <template v-if="report.data.value"
      ><p>
        原报告 {{ report.data.value.summary.status }} ·
        {{
          report.data.value.summary.live === true
            ? "历史真实执行"
            : report.data.value.summary.live === false
              ? "离线执行"
              : "按原报告解释"
        }}
        · 差例 {{ report.data.value.error_count }} · 本页
        {{ report.data.value.errors.length }}
      </p>
      <RawDetails
        title="原报告冻结范围与选中会话"
        :value="report.data.value.summary" />
      <div v-for="(metric, mode) in report.data.value.metrics" :key="mode">
        <h3>{{ mode }}</h3>
        <RawDetails title="原指标（空分母保持未知）" :value="metric" />
      </div>
      <p v-for="(r, i) in report.data.value.errors" :key="i">
        {{ r.mode }} / {{ r.case_id }}：{{ r.text }} →
        {{ JSON.stringify(r.score) }}
      </p>
      <el-button
        v-if="report.data.value.next_offset !== null"
        @click="readReport(report.data.value.next_offset)"
        >下一页差例</el-button
      >
      <p>历史 PASS 只属于原字节和样本；本次浏览不证明当前模型质量。</p>
      <RawDetails :value="report.data.value"
    /></template>
    <h3>Langfuse 历史映射</h3>
    <label for="evaluation-import">受控历史子集</label
    ><select id="evaluation-import" v-model="importId">
      <option v-for="id in catalog.platform_imports" :key="id">{{ id }}</option>
    </select>
    <p v-if="!catalog.platform_imports.length">当前目录未配置平台历史映射。</p>
    <el-button
      :disabled="!importId"
      @click="
        mapping.load(
          '/learning/evaluation/imports/' + encodeURIComponent(importId),
        )
      "
      >读取映射与平台链接</el-button
    ><StateNotice
      :error="mapping.error.value"
      :loading="mapping.loading.value"
    /><template v-if="mapping.data.value"
      ><p>
        {{ mapping.data.value.state.status }} · 预期
        {{ mapping.data.value.expected_rows }} 行 · 完整会话
        {{ mapping.data.value.summary.selected_case_ids.length }} · 原门禁
        {{ mapping.data.value.summary.release_gate.accepted ? "通过" : "拒绝" }}
      </p>
      <a
        v-if="safeUrl(mapping.data.value.state.dataset_url)"
        :href="safeUrl(mapping.data.value.state.dataset_url)"
        target="_blank"
        rel="noopener"
        >Langfuse Dataset</a
      ><a
        v-if="safeUrl(mapping.data.value.state.experiment_url)"
        :href="safeUrl(mapping.data.value.state.experiment_url)"
        target="_blank"
        rel="noopener"
        >Langfuse Experiment</a
      ><RawDetails :value="mapping.data.value" /></template
    ><el-checkbox v-model="confirmed" :disabled="!importId"
      >确认上传受控历史子集 · 0 模型</el-checkbox
    ><el-button
      :disabled="!importId || !confirmed || !canStart(catalog.tasks)"
      :loading="action.busy.value"
      @click="upload"
      >上传 / 核对原映射</el-button
    ><StateNotice :error="action.error.value" />
    <p v-if="catalog.prompt">
      Prompt 工件 {{ catalog.prompt.name }} v{{ catalog.prompt.version }} ·
      请求摘要 {{ catalog.prompt.request_digest }}；运行仅消费已发布版本。
    </p>
    <p v-else>当前目录未配置平台 Prompt 工件；原完整版本仍绑定其实际文本。</p>
  </section>
</template>
