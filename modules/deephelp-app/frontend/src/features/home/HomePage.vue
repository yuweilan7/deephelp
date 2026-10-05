<script setup lang="ts">
import { workspace } from "../../composables/workspace";
</script>
<template>
  <div class="home">
    <p class="eyebrow">从一条问题开始</p>
    <h1>把一次回答的来龙去脉看清楚</h1>
    <p class="lead">
      先准备个人业务资料，再提出问题。沿着同一个会话与运行编号，阅读事实、证据和执行过程。
    </p>
    <div class="start-grid">
      <router-link to="/conversation" class="card"
        ><b>01 · 运行一段对话</b>
        <p>提问 → 补充 → 阅读原事实 → 核对重投</p></router-link
      ><router-link to="/business" class="card"
        ><b>02 · 准备个人业务资料</b>
        <p>
          创建订单、优惠券与活动，再核对工具查询的实际金额与版本
        </p></router-link
      ><a
        href="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/README.md"
        target="_blank"
        rel="noopener"
        class="card"
        ><b>03 · 从 Java 走进 Agent</b>
        <p>十四章课程、十一项离线实验与练习答案</p></a
      >
    </div>
    <section class="card">
      <h2>当前运行方式</h2>
      <p v-if="workspace.connection">
        模式 {{ workspace.connection.execution.mode }} · 事实来源
        {{
          workspace.connection.execution.business_source
        }}。这里显示服务端配置，连接不代表所有下游健康。
      </p>
      <p>
        浏览、刷新历史和阅读教程新增模型调用 0
        次。只有明确执行才进入当前共享预算。
      </p>
    </section>
  </div>
</template>
