<script setup lang="ts">
import { computed, watch } from "vue";
import type { RunResponse } from "../../api/types";
import { workspace } from "../../composables/workspace";
import { useRead } from "../../composables/requests";
import { explanationPath, type Explanation } from "../../api/explanation";
import { statusLabel, factLabel } from "../../content/navigation";
import StateNotice from "../../components/StateNotice.vue";
import RawDetails from "../../components/RawDetails.vue";
import MechanismGuide from "../../components/MechanismGuide.vue";
const read = useRead<Explanation>();
const props = defineProps<{
  target?: { run_id: string; session_id: string };
  returned?: RunResponse | null;
}>();
const target = computed(
  () =>
    props.target ??
    (workspace.selected?.response.run_id
      ? {
          run_id: workspace.selected.response.run_id,
          session_id: workspace.selected.session_id,
        }
      : null),
);
const returned = computed(() =>
  props.target ? (props.returned ?? null) : workspace.selected?.response,
);
watch(
  target,
  (s) => {
    read.clear();
    if (s?.run_id) void read.load(explanationPath(s.run_id, s.session_id));
  },
  { immediate: true },
);
</script>
<template>
  <StateNotice :loading="read.loading.value" :error="read.error.value" />
  <template v-if="read.data.value">
    <MechanismGuide mechanism="request" />
    <h3>这次发生了什么</h3>
    <p class="user-text">{{ read.data.value.input.raw_text }}</p>
    <p>{{ read.data.value.summary }}</p>
    <p v-if="read.data.value.receipt?.missing_slots?.length">
      缺少必要资料：{{
        read.data.value.receipt.missing_slots.map(factLabel).join("、")
      }}；只有原补充绑定校验通过后才继续。
    </p>
    <p v-if="read.data.value.receipt?.error">
      原错误 {{ read.data.value.receipt.error.code }} ·
      {{ read.data.value.receipt.error.message }}
    </p>
    <p>
      允许的下一步（原next_action）：{{
        read.data.value.receipt?.next_action ??
        "没有终态动作记录，先核对原状态"
      }}。页面不会自动执行它。
    </p>
    <p>
      PG 原状态 {{ statusLabel(read.data.value.status) }} · 当前问题版本
      {{ read.data.value.question_now.version }} · 原运行问题版本
      {{ read.data.value.question_at_run?.version ?? "未知" }}
    </p>
    <p>
      原执行模型
      {{ read.data.value.receipt?.call_counts.model_calls ?? "未知" }} / 工具
      {{
        read.data.value.receipt?.call_counts.tool_calls ?? "未知"
      }}；所选返回模型
      {{ returned?.call_counts.model_calls ?? "无本次执行返回" }} / 工具
      {{ returned?.call_counts.tool_calls ?? "无本次执行返回" }}。
    </p>
    <p v-if="returned?.replayed">
      这次读取的是原 receipt。原执行 trace
      {{ read.data.value.trace_id }}；本次重投 trace
      {{ returned.trace_id }}。重投没有重新执行原阶段。
    </p>
    <h3>采用了哪些资料</h3>
    <p v-if="!read.data.value.entities">
      实体诊断未记录或已丢失；已保存的原回执仍有效。
    </p>
    <div
      v-for="(e, i) in read.data.value.entities?.observations"
      :key="i"
      class="route-result"
    >
      <b>{{ factLabel(e.name) }} · {{ e.value }}</b>
      <p>
        {{
          e.disposition === "negated"
            ? "被否定，不能当成确认值"
            : e.disposition === "correction"
              ? "更正观察，核对原问题冲突与采用版本"
              : "本次观察"
        }}
        · {{ e.layer }} · 原文区间 [{{ e.start }}, {{ e.end }})
      </p>
      <p>来源消息 {{ e.source.message_id }} · “{{ e.source.excerpt }}”</p>
    </div>
    <RawDetails
      v-if="read.data.value.entities?.conflicts.length"
      title="原更正与冲突来源"
      :value="read.data.value.entities.conflicts"
    />
    <p>
      归属记录
      {{
        read.data.value.event?.judgement?.relation ??
        read.data.value.event?.disposition ??
        "未记录"
      }}
      · 原因
      {{
        read.data.value.event?.edges.map((e) => e.reason).join("、") ||
        read.data.value.event?.diagnostics.join("、") ||
        "未知"
      }}。补充不会自动批准旧操作。
    </p>
    <h3>这次走了哪条路</h3>
    <p>
      最终意图 {{ read.data.value.decision?.final_code ?? "未确定" }} · 决策
      {{ read.data.value.decision?.decision ?? "未知" }} · 原因
      {{
        read.data.value.decision?.reason_code ?? "未记录"
      }}。候选相似不代表业务在支持范围内。
    </p>
    <p v-for="(c, i) in read.data.value.decision?.cascade_steps" :key="i">
      {{ c.layer }} → {{ c.action }} · 已记录原因 {{ c.reason }} · 模型
      {{ c.model_calls }} / 检索 {{ c.retrieval_calls }}
    </p>
    <div
      v-for="s in read.data.value.stages"
      :key="s.stage"
      class="route-result"
    >
      <b>{{ s.title }} · {{ statusLabel(s.status) }}</b>
      <p>{{ s.purpose }}</p>
      <small
        >{{ s.stage }} · {{ s.elapsed_ms.toFixed(2) }} ms ·
        {{ s.started_at ?? "开始未知" }} → {{ s.finished_at ?? "结束未知" }}
        <span v-if="s.error_code">· {{ s.error_code }}</span></small
      >
    </div>
    <p v-if="!read.data.value.stages.length">
      阶段未记录；没有用预设动画补全路径。
    </p>
    <h3>哪些内容可以确定</h3>
    <p>
      模型动作是建议，工具结果是下游记录；代码核验后才进入原
      receipt。下方不可变证据可继续读回。
    </p>
    <p v-for="t in read.data.value.tools" :key="t.call_id">
      实际工具 {{ t.tool_name ?? t.name ?? "名称见原记录" }} · call
      {{ t.call_id }} · {{ t.result?.status ?? t.error_code ?? "状态未知" }}
    </p>
    <p v-if="read.data.value.gaps.length" class="warning">
      诊断缺口：{{
        read.data.value.gaps.join("、")
      }}。缺观测不能推断业务未执行。
    </p>
    <details>
      <summary>来源与阅读边界</summary>
      <p v-for="b in read.data.value.boundaries" :key="b">{{ b }}</p>
    </details>
    <RawDetails title="PG 回执与只读语义投影" :value="read.data.value" />
  </template>
</template>
