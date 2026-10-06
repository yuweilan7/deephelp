<script setup lang="ts">
import PageGuide from "../../components/PageGuide.vue";
import RulesLab from "./RulesLab.vue";
import RetrievalLab from "./RetrievalLab.vue";
import CorpusAuthor from "./CorpusAuthor.vue";
</script>
<template>
  <PageGuide
    title="识别与检索"
    goal="把原文规则、实际分类与检索候选分开观察，理解每一步的证据边界。"
    needs="规则实验零模型；检索比较需要 Retrieval MCP / Embedding；实际分类先选原 run"
    :steps="[
      '运行否定 / 更正规则反例',
      '在对话选一条事实 / 时间线',
      '读取同次运行意图',
      '核对预算后明确执行四路检索比较',
    ]"
    expected="规则展示原文区间；分类读取原诊断；检索展示同向量候选、原分数及重排状态。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/10-retrieval.md"
  />
  <div class="two-column"><RulesLab /><RetrievalLab /></div>
  <CorpusAuthor />
</template>
