<script setup lang="ts">
import { ref, onMounted } from "vue";
import { connect } from "../../composables/workspace";
import type { Catalog, Plan } from "../../api/evaluation";
import { useRead } from "../../composables/requests";
import PageGuide from "../../components/PageGuide.vue";
import StateNotice from "../../components/StateNotice.vue";
import ReportPanel from "./ReportPanel.vue";
import CandidatePanel from "./CandidatePanel.vue";
import TaskPanel from "./TaskPanel.vue";
import ReleasePanel from "./ReleasePanel.vue";
import IndependentPanel from "./IndependentPanel.vue";
import PairedPanel from "./PairedPanel.vue";
const catalog = useRead<Catalog>(),
  plan = useRead<Plan>(),
  taskId = ref("");
async function refresh() {
  await Promise.all([
    catalog.load("/learning/evaluation", true),
    plan.load("/learning/evaluation/plan", true),
    connect(),
  ]);
}
onMounted(refresh);
</script>
<template>
  <PageGuide
    title="评测与改进"
    goal="从冻结差例走向独立审核、候选验证与完整版本发布，每一步都保留证据边界。"
    needs="受控评测目录、原个人 owner、PG；真实任务另需足够模型预算"
    :steps="[
      '读原报告范围 / 指标 / 差例',
      '采集独立来源并人工审核 revision',
      '核对计划，明确启动候选构建与内容验证',
      '确认精确门禁、hash 和当前 revision 后发布',
    ]"
    expected="浏览与读取 0 模型；单 worker、有界尝试、累计预算和 INTERRUPTED 保留；候选不会自动成为 active。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/14-evaluation-release.md"
  /><el-button @click="refresh">刷新受控目录与执行计划 · 0 模型</el-button
  ><StateNotice
    :error="catalog.error.value || plan.error.value"
    :loading="catalog.loading.value || plan.loading.value"
  />
  <p v-if="catalog.error.value">
    评测目录不可用，请核对服务端 owner 与 catalog
    配置，恢复后刷新。当前业务对话可按其自身依赖继续。
  </p>
  <template v-if="catalog.data.value"
    ><IndependentPanel />
    <PairedPanel @task="(id) => (taskId = id)" />
    <p>
      当前工具冻结集 {{ catalog.data.value.dataset.cases }} 会话 /
      {{ catalog.data.value.dataset.turns }} 消息 · 独立审计
      {{ catalog.data.value.dataset.independent_cases }} · 来源组
      {{ catalog.data.value.dataset.declared_source_groups }} ·
      {{
        JSON.stringify(catalog.data.value.dataset.split_counts)
      }}。历史报告范围另见原报告。
    </p>
    <div class="two-column evaluation-grid">
      <div>
        <ReportPanel
          :catalog="catalog.data.value"
          @refresh="refresh"
          @task="(id) => (taskId = id)"
        /><CandidatePanel
          :catalog="catalog.data.value"
          @refresh="refresh"
          @task="(id) => (taskId = id)"
        />
      </div>
      <div>
        <TaskPanel
          v-model:id="taskId"
          :catalog="catalog.data.value"
          :plan="plan.data.value"
          @refresh="refresh"
        /><ReleasePanel :catalog="catalog.data.value" @refresh="refresh" />
      </div></div
  ></template>
</template>
