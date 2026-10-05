<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../api/client";
import type { RulesResult } from "../../api/learning";
import { useAction, useRead } from "../../composables/requests";
import { workspace } from "../../composables/workspace";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const text = ref("不是订单90220000，是订单90234105，优惠未到账"),
  result = ref<RulesResult | null>(null),
  action = useAction(),
  run = useRead<unknown>();
watch(
  () => workspace.selected,
  () => run.clear(),
);
function execute() {
  void action.run(async () => {
    result.value = await api.request<RulesResult>("/learning/rules", "POST", {
      text: text.value,
    });
  });
}
function inspect() {
  const s = workspace.selected;
  if (s?.response.run_id)
    void run.load(
      `/debug/runs/${encodeURIComponent(s.response.run_id)}/intent?session_id=${encodeURIComponent(s.session_id)}`,
    );
}
</script>
<template>
  <section class="card">
    <h2>原文、否定与更正规则</h2>
    <p>
      这项实验运行原实体规则，新增模型调用 0 次；规则候选不等于完整 600 分类。
    </p>
    <form @submit.prevent="execute">
      <label for="rules-text">规则练习原文</label
      ><el-input
        id="rules-text"
        v-model="text"
        type="textarea"
        :maxlength="2000"
        :rows="3"
      /><el-button native-type="submit" :loading="action.busy.value"
        >执行原规则 · 0 模型</el-button
      >
    </form>
    <StateNotice :error="action.error.value" /><template v-if="result"
      ><p>
        实际模型调用 {{ result.model_calls }} · 确认
        {{ result.confirmed.length }} · 冲突 {{ result.conflicts.length }} ·
        未决 {{ result.unresolved.length }}
      </p>
      <p v-for="(o, i) in result.observations" :key="i">
        [{{ o.start }}, {{ o.end }}) {{ o.source.excerpt }} → {{ o.name }} /
        {{ o.disposition }}
      </p>
      <RawDetails :value="result"
    /></template>
    <p v-else>尚未运行。输入含“不是…是…”的更正句，观察原文区间和确认项。</p>
    <h3>已选运行的实际分类</h3>
    <p>
      会话 {{ workspace.selected?.session_id || "未选择" }} · run
      {{ workspace.selected?.response.run_id || "未选择" }}
    </p>
    <el-button :disabled="!workspace.selected" @click="inspect"
      >读取同次运行意图</el-button
    ><StateNotice
      :error="run.error.value"
      :loading="run.loading.value"
    /><RawDetails v-if="run.data.value" :value="run.data.value" />
  </section>
</template>
