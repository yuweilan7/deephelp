<script setup lang="ts">
import { watch } from "vue";
import { workspace } from "../../composables/workspace";
import { useRead } from "../../composables/requests";
import { explanationPath, type Explanation } from "../../api/explanation";
import { safeUrl, type Observation } from "../../api/learning";
import PageGuide from "../../components/PageGuide.vue";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
import MechanismGuide from "../../components/MechanismGuide.vue";
const run = useRead<Explanation>(),
  observation = useRead<Observation>();
watch(
  () => workspace.selected,
  () => {
    run.clear();
    observation.clear();
  },
);
async function inspect(cursor = 0) {
  const s = workspace.selected;
  if (!s?.response.run_id) return;
  await Promise.all([
    run.load(explanationPath(s.response.run_id, s.session_id)),
    observation.load(
      `/observability/runs/${encodeURIComponent(s.response.run_id)}?session_id=${encodeURIComponent(s.session_id)}&cursor=${cursor}&page_size=100`,
    ),
  ]);
}
function start(value: string) {
  return new Date(Number(value) / 1_000_000).toLocaleString();
}
</script>
<template>
  <PageGuide
    title="观测诊断"
    goal="沿实际节点、模型attempt和原receipt，分清业务确认与诊断缺口。"
    needs="先在对话选中原run；平台不可用保留原事实"
    :steps="[
      '读取同次运行',
      '沿真实父子和开始结束看等待',
      '只计实际GENERATION并核对原执行计数',
      '按前序读审批恢复，再读对应课程',
    ]"
    expected="浏览新增模型/工具0次；没有排队、CPU、完整usage证据就保持未知。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/13-observations.md"
  />
  <section class="card">
    <MechanismGuide mechanism="observation" />
    <h2>当前关联</h2>
    <p class="identifier">
      session {{ workspace.selected?.session_id || "未选择" }} · run
      {{ workspace.selected?.response.run_id || "未选择" }}
    </p>
    <el-button :disabled="!workspace.selected" @click="inspect()"
      >读取同次运行观测 · 0 模型</el-button
    >
    <StateNotice
      :error="run.error.value || observation.error.value"
      :loading="run.loading.value || observation.loading.value"
    />
    <p v-if="!workspace.selected">先到对话页选一条已有结果。</p>
    <p v-if="run.data.value">
      {{ run.data.value.summary }} 原执行trace {{ run.data.value.trace_id }} ·
      模型 {{ run.data.value.receipt?.call_counts.model_calls ?? "未知" }} /
      工具
      {{
        run.data.value.receipt?.call_counts.tool_calls ?? "未知"
      }}；所选返回模型
      {{ workspace.selected?.response.call_counts.model_calls }} / 工具
      {{
        workspace.selected?.response.call_counts.tool_calls
      }}。当前浏览新增调用0。
    </p>
    <template v-if="observation.data.value">
      <p>
        来源 {{ observation.data.value.source }} · 平台
        {{ observation.data.value.status }} · 读取到
        {{ observation.data.value.total }}
        条。unavailable/retired/incomplete/truncated分别保留；缺span不能推翻PG已提交事实。
      </p>
      <template v-if="observation.data.value.explanation">
        <h3>实际用量与分母</h3>
        <p>
          去重GENERATION
          {{ observation.data.value.explanation.unique_generations }}
          次；有完整usage
          {{ observation.data.value.explanation.known_usage_attempts }} 次，未知
          {{ observation.data.value.explanation.unknown_usage_attempts }}
          次。已知token小计
          {{
            observation.data.value.explanation.known_token_subtotal
          }}，本次有界读回总量
          {{
            observation.data.value.explanation.actual_total_tokens ?? "未知"
          }}。去掉重复记录
          {{ observation.data.value.explanation.duplicates_removed }} 条；冲突
          {{ observation.data.value.explanation.conflicting_observations }} 条。
        </p>
        <p>
          统计覆盖本次平台有界读回的全部记录，当前页只展示其中一页；不把当前页条数当原运行次数。
        </p>
        <p v-for="n in observation.data.value.explanation.notes" :key="n">
          {{ n }}
        </p>
        <p
          v-for="e in observation.data.value.explanation.trace_extents"
          :key="e.trace_id"
        >
          trace {{ e.trace_id }} · 已观察节点包围范围
          {{ e.observed_extent_ms?.toFixed(2) ?? "未知" }} ms · 起止完整
          {{ e.complete_intervals }}。请求总耗时/排队/CPU须各有观测。
        </p>
      </template>
      <h3>真实节点与等待</h3>
      <p>
        只画当前页实际节点；parent缺页表示父记录未展示，不能补一个执行节点。主链13段与审批图是不同范围。
      </p>
      <ol class="observed-timeline">
        <li
          v-for="s in observation.data.value.spans"
          :key="s.trace_id + s.span_id"
          class="route-result"
        >
          <b>{{ s.service }} · {{ s.name }} · {{ s.type }}</b>
          <p>
            {{ start(s.start_unix_ns) }} · 耗时
            {{ s.duration_ms?.toFixed(2) ?? "未知" }} ms ·
            {{ s.incomplete ? "尚无完整结束记录" : "已记录结束" }}
          </p>
          <p class="identifier">
            节点 {{ s.span_id }} ← 父 {{ s.parent_span_id ?? "无父记录" }} ·
            trace {{ s.trace_id }}
          </p>
          <p>
            状态 {{ s.attributes["deephelp.status"] ?? "未记录" }} · 错误
            {{ s.attributes["deephelp.error_code"] ?? "未记录" }} · PID
            {{ s.attributes["service.instance.id"] ?? "未记录" }} · 版本
            {{ s.version ?? "未知" }} · profile
            {{ s.attributes["deephelp.profile"] ?? "未记录" }}
          </p>
          <template v-if="s.type === 'GENERATION'"
            ><p>
              模型 {{ s.model ?? "未知" }} · attempt
              {{ s.attributes["deephelp.attempt"] ?? "未知" }} · retry
              {{ s.attributes["deephelp.retry"] ?? "未知" }} · usage
              {{ s.usage ? JSON.stringify(s.usage) : "未知" }}
            </p>
            <RawDetails
              title="受控输入/输出与schema或原生动作结构（不含隐藏推理）"
              :value="{ input: s.input, output: s.output }"
          /></template>
        </li>
      </ol>
      <h3>暂停与恢复前序</h3>
      <p v-for="(a, i) in observation.data.value.associations" :key="i">
        {{ a.kind }} · {{ a.trace_id }} / {{ a.span_id }} ←
        {{ a.predecessor_trace_id ?? "无登记前序" }} /
        {{ a.predecessor_span_id ?? "无登记前序" }}
      </p>
      <p>
        这里读取持久关联；不在浏览时resume。新恢复运行不能冒充原暂停仍连续执行，取消请求也不能当作资源清理或副作用结果。
      </p>
      <a
        v-if="safeUrl(observation.data.value.langfuse_url)"
        :href="safeUrl(observation.data.value.langfuse_url)"
        target="_blank"
        rel="noopener"
        >在受保护Langfuse看同run</a
      >
      <a
        v-if="safeUrl(observation.data.value.grafana_url)"
        :href="safeUrl(observation.data.value.grafana_url)"
        target="_blank"
        rel="noopener"
        >在受保护Grafana看实际指标</a
      >
      <p>
        指标是进程/时间窗口统计，有窗口和分母才可比较；不由某条run或单个span推算全局成功率。
      </p>
      <el-button
        v-if="observation.data.value.next_cursor !== null"
        @click="inspect(observation.data.value.next_cursor)"
        >下一页实际观测</el-button
      >
      <RawDetails title="实际平台记录与前序" :value="observation.data.value" />
    </template>
    <RawDetails
      v-if="run.data.value"
      title="PG原回执与预算（actual/预留/估算分开）"
      :value="run.data.value"
    />
  </section>
</template>
