<script setup lang="ts">
import PageGuide from "../../components/PageGuide.vue";
import ProtocolLab from "./ProtocolLab.vue";
import WorkflowLab from "./WorkflowLab.vue";
</script>
<template>
  <PageGuide
    title="工具与工作流"
    goal="沿固定动作理解 MCP 校验、SOP 计划和等待审批 / 恢复。"
    needs="Business MCP；工作流读取原配置，合成审批仅限原独立身份与执行口"
    :steps="[
      '读取实际协议与 schema',
      '查询个人订单或执行固定非法参数反例',
      '读取 SOP 与实际图',
      '原身份核对合成操作后明确审批 / 恢复',
    ]"
    expected="协议反例拒绝进入业务查询；审批保持原参数 / 版本 / 快照，未知效果先对账。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/12-approval.md"
  />
  <div class="two-column"><ProtocolLab /><WorkflowLab /></div>
</template>
