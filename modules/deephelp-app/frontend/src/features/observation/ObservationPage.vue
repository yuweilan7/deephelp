<script setup lang="ts">
import { watch } from "vue";
import { workspace } from "../../composables/workspace";
import { useRead } from "../../composables/requests";
import type { Flow } from "../../api/types";
import { safeUrl, type Observation } from "../../api/learning";
import PageGuide from "../../components/PageGuide.vue";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const flow = useRead<Flow>(),
  observation = useRead<Observation>();
watch(
  () => workspace.selected,
  () => {
    flow.clear();
    observation.clear();
  },
);
async function inspect(cursor = 0) {
  const s = workspace.selected;
  if (!s?.response.run_id) return;
  await Promise.all([
    flow.load(
      `/debug/runs/${encodeURIComponent(s.response.run_id)}/flow?session_id=${encodeURIComponent(s.session_id)}`,
    ),
    observation.load(
      `/observability/runs/${encodeURIComponent(s.response.run_id)}?session_id=${encodeURIComponent(s.session_id)}&cursor=${cursor}&page_size=100`,
    ),
  ]);
}
</script>
<template>
  <PageGuide
    title="观测诊断"
    goal="读取当前原 run 的专业平台记录，将实际状态、usage 与业务结果一起核对。"
    needs="先在对话选中原 run；平台不可用必须明确显示"
    :steps="[
      '在主对话点击事实 / 时间线',
      '回到本页读取同次运行',
      '比较原执行计数与实际 GENERATION usage',
      '在受保护 Langfuse 打开同次运行',
    ]"
    expected="诊断失败不改变原业务 receipt；未知 usage 保持未知，浏览新增模型调用 0。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/13-observations.md"
  />
  <section class="card">
    <h2>当前关联</h2>
    <p class="identifier">
      session {{ workspace.selected?.session_id || "未选择" }} · run
      {{ workspace.selected?.response.run_id || "未选择" }}
    </p>
    <el-button :disabled="!workspace.selected" @click="inspect()"
      >读取同次运行观测 · 0 模型</el-button
    ><StateNotice
      :error="flow.error.value || observation.error.value"
      :loading="flow.loading.value || observation.loading.value"
    />
    <p v-if="!workspace.selected">先到对话页选择一条已有结果。</p>
    <template v-if="observation.data.value"
      ><p>
        来源 {{ observation.data.value.source }} · 平台
        {{ observation.data.value.status }} · 当前 run 记录
        {{ observation.data.value.total }} · 原 trace
        {{ flow.data.value?.trace_id || "未知" }}
      </p>
      <p>
        本页 GENERATION
        {{
          observation.data.value.spans.filter((s) => s.type === "GENERATION")
            .length
        }}
        条。计数来自实际页，不代表整个运行模型次数。
      </p>
      <div
        v-for="(s, i) in observation.data.value.spans.filter(
          (s) => s.type === 'GENERATION',
        )"
        :key="i"
        class="route-result"
      >
        <b>{{ s.model || "模型未知" }}</b>
        <p>
          版本 {{ s.version || "未知" }} · usage
          {{ s.usage ? JSON.stringify(s.usage) : "未知" }}
        </p>
      </div>
      <p>
        原执行计数
        {{
          JSON.stringify(flow.data.value?.data.response?.call_counts) ?? "未知"
        }}
        · 所选返回计数
        {{ JSON.stringify(workspace.selected?.response.call_counts) }} ·
        当前浏览新增调用 0
      </p>
      <p>
        <a
          v-if="safeUrl(observation.data.value.langfuse_url)"
          :href="safeUrl(observation.data.value.langfuse_url)"
          target="_blank"
          rel="noopener"
          >在受保护 Langfuse 查看同一运行</a
        ><a
          v-if="safeUrl(observation.data.value.grafana_url)"
          :href="safeUrl(observation.data.value.grafana_url)"
          target="_blank"
          rel="noopener"
          >在受保护 Grafana 查看同一 trace</a
        >
      </p>
      <el-button
        v-if="observation.data.value.next_cursor !== null"
        @click="inspect(observation.data.value.next_cursor)"
        >下一页实际观测</el-button
      ><RawDetails
        title="实际平台记录与恢复前序"
        :value="observation.data.value" /></template
    ><RawDetails
      v-if="flow.data.value"
      title="原业务 flow"
      :value="flow.data.value"
    />
  </section>
</template>
