<script setup lang="ts">
import { ref } from "vue";
import { api } from "../../api/client";
import type { RetrievalResult } from "../../api/learning";
import { useAction } from "../../composables/requests";
import { workspace } from "../../composables/workspace";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const text = ref("优惠券达到门槛却不能使用"),
  result = ref<RetrievalResult | null>(null),
  confirmed = ref(false),
  action = useAction();
function execute() {
  if (!confirmed.value) return;
  void action.run(async () => {
    result.value = await api.request<RetrievalResult>(
      "/learning/retrieval",
      "POST",
      { text: text.value },
    );
    confirmed.value = false;
    await api.connect();
  });
}
</script>
<template>
  <section class="card">
    <h2>同向量四路检索</h2>
    <p>
      Dense / BM25 / Hybrid / Hybrid + Rerank
      复用同一实际向量与召回入口，排名和分数保持原记录。
    </p>
    <form @submit.prevent="execute">
      <label for="retrieval-text">比较查询</label
      ><el-input
        id="retrieval-text"
        v-model="text"
        type="textarea"
        :rows="3"
        :maxlength="2000"
      />
      <p>
        明确执行才请求真实 Embedding，预计 1 次（缓存可为 0）；无 chat /
        推理请求。是否重排及用量以实际结果为准。共享剩余
        {{ workspace.connection?.execution.remaining_calls ?? "未知" }}
        次预约；已计
        {{ workspace.connection?.execution.charged_tokens ?? "未知" }} / 上限
        {{ workspace.connection?.execution.max_tokens ?? "未知" }} tokens；单次
        90 秒。
      </p>
      <el-checkbox v-model="confirmed">已核对查询与共享预算</el-checkbox
      ><el-button
        native-type="submit"
        type="primary"
        :disabled="!confirmed"
        :loading="action.busy.value"
        >明确执行检索比较</el-button
      >
    </form>
    <StateNotice :error="action.error.value" />
    <p v-if="!result">尚未运行。浏览本页不会自动生成向量。</p>
    <template v-else
      ><p>
        实际耗时 {{ result.elapsed_ms.toFixed(1) }} ms · 模型调用
        {{ result.receipt.model_calls }} · trace {{ result.trace_id }}
      </p>
      <div
        v-for="(route, mode) in result.routes"
        :key="mode"
        class="route-result"
      >
        <h3>{{ mode }}</h3>
        <p v-if="route.rerank">
          重排 {{ route.rerank.status }} ·
          {{ route.rerank.reason || "已重排" }} ·
          {{ route.rerank.policy.model }} · 候选
          {{ route.rerank.input_doc_ids.length }} /
          {{ route.rerank.policy.candidate_budget }} ·
          {{ route.rerank.elapsed_ms }} ms · usage
          {{ route.rerank.result?.usage?.total_tokens ?? "未知" }}
        </p>
        <p v-for="c in route.candidates" :key="c.evidence_doc_id">
          {{ c.rank }}. {{ c.intent_code }} · 证据 {{ c.evidence_doc_id }} · 原
          {{ c.score_kind }} {{ c.raw_score }}
        </p>
        <details>
          <summary>原召回文档与重排分数</summary>
          <p v-for="h in route.hits" :key="h.doc_id">
            {{ h.doc_id }} · {{ h.intent_code }} · 原名次 {{ h.rank }} · 原
            {{ h.score_kind }} {{ h.raw_score }} · rerank
            {{
              route.rerank?.result?.hits.find(
                (r) => r.document.doc_id === h.doc_id,
              )?.score ?? "未运行"
            }}
            · {{ h.content }}
          </p>
        </details>
      </div>
      <RawDetails :value="result"
    /></template>
  </section>
</template>
