<script setup lang="ts">
import PageGuide from "../../components/PageGuide.vue";
import ProtocolLab from "./ProtocolLab.vue";
import WorkflowLab from "./WorkflowLab.vue";
</script>
<template>
  <PageGuide
    title="工具与工作流"
    goal="编辑并校验独立 SOP 草稿，沿固定动作理解 MCP、SOP 与等待审批 / 恢复。"
    needs="草稿仅需个人工作区；协议调用需 Business MCP，合成审批仅限原独立身份与执行口"
    :steps="[
      '读取实际协议与 schema',
      '查询个人订单或执行固定非法参数反例',
      '读取 SOP 与实际图',
      '创建草稿，编辑节点和有限条件，保存校验后刷新重载',
      '原身份核对合成操作后明确审批 / 恢复',
    ]"
    expected="草稿返回真实 revision 与错误位置，不自动改变正式流程；协议反例拒绝进入业务查询，审批及未知效果保持原守卫。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/12-approval.md"
  />
  <div class="two-column"><ProtocolLab /><WorkflowLab /></div>
</template>
