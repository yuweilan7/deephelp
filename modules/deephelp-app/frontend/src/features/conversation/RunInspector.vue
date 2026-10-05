<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from "vue";
import { api } from "../../api/client";
import type { Flow } from "../../api/types";
import { workspace } from "../../composables/workspace";
import { LatestRead } from "../../composables/latest";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
import { statusLabel, factLabel } from "../../content/navigation";
import RunExplanation from "./RunExplanation.vue";
const read = new LatestRead();
const flow = ref<Flow | null>(null),
  error = ref(""),
  loading = ref(false),
  evidence = ref<unknown>(null);
watch(
  () => workspace.selected,
  async (selection) => {
    flow.value = null;
    evidence.value = null;
    error.value = "";
    read.cancel();
    if (!selection?.response.run_id) {
      loading.value = false;
      return;
    }
    loading.value = true;
    try {
      await read.run(
        (signal) =>
          api.request<Flow>(
            `/debug/runs/${encodeURIComponent(selection.response.run_id!)}/flow?session_id=${encodeURIComponent(selection.session_id)}`,
            "GET",
            undefined,
            signal,
          ),
        (d) => {
          if (
            d.run_id !== selection.response.run_id ||
            (!selection.response.replayed &&
              d.trace_id !== selection.response.trace_id)
          )
            throw new Error("诊断与所选运行不一致，请重新读取。");
          flow.value = d;
          loading.value = false;
        },
      );
    } catch (e) {
      error.value = (e as Error).message;
      loading.value = false;
    }
  },
  { immediate: true },
);
async function getEvidence(id: string) {
  const selected = workspace.selected;
  try {
    const d = await api.request(`/business/evidence/${encodeURIComponent(id)}`);
    if (selected === workspace.selected) evidence.value = d;
  } catch (e) {
    if (selected === workspace.selected) error.value = (e as Error).message;
  }
}
onBeforeUnmount(() => read.cancel());
</script>
<template>
  <section class="card inspector">
    <h2>事实与执行证据</h2>
    <p v-if="!workspace.selected" class="muted">
      选中一条结果的“事实 / 时间线”，沿用原 run 阅读。
    </p>
    <template v-else
      ><p class="identifier">
        会话 {{ workspace.selected.session_id }}<br />run
        {{ workspace.selected.response.run_id }}
      </p>
      <el-tag>{{ statusLabel(workspace.selected.response.outcome) }}</el-tag>
      <RunExplanation />
      <dl class="facts">
        <template v-for="(f, i) in workspace.selected.response.facts" :key="i"
          ><dt>{{ factLabel(f.name) }}</dt>
          <dd>
            {{
              typeof f.value === "object" ? JSON.stringify(f.value) : f.value
            }}
          </dd></template
        >
      </dl>
      <p v-if="!workspace.selected.response.facts.length">
        此结果没有业务事实，请按回复补充信息或处理失败。
      </p>
      <RawDetails
        title="原事实、不可变证据与版本"
        :value="workspace.selected.response" /><el-button
        v-for="e in workspace.selected.response.evidence_refs.filter(
          (e) => e.evidence_id,
        )"
        :key="e.evidence_id"
        @click="getEvidence(e.evidence_id!)"
        >读取证据 {{ e.evidence_id }}</el-button
      ><RawDetails
        v-if="evidence"
        title="Business 原版本证据"
        :value="evidence" />
      <h3>13 段主链 · 原执行</h3>
      <div
        v-for="s in workspace.selected.response.stages"
        :key="s.stage"
        class="stage"
      >
        <b>{{ s.stage }}</b
        ><span
          >{{ s.status }} · {{ s.elapsed_ms }} ms · 模型 {{ s.model_calls }} /
          工具 {{ s.tool_calls }}</span
        >
      </div>
      <StateNotice :error="error" :loading="loading" />
      <p v-if="error">诊断不可用时，已保存的业务事实仍按原 receipt 读取。</p>
      <RawDetails v-if="flow" title="原 SOP / MCP / 检索诊断" :value="flow"
    /></template>
  </section>
</template>
