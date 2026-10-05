<script setup lang="ts">
import type { AgentReceipt, AgentTaskRecord } from "../../api/types";
import RawDetails from "../../components/RawDetails.vue";
defineProps<{
  receipts?: AgentReceipt[] | null;
  records?: AgentTaskRecord[] | null;
  source?: string;
}>();
</script>
<template>
  <section>
    <h3>原运行的专业 Agent 委托</h3>
    <p>
      在对话明确选择协作，已确认主意图和槽位后才进入。协调者建议委托，专业角色有独立任务上下文；代码核对工具、参数和证据，原
      SOP 决定分支与回复。读取这里新增模型 0 次。
    </p>
    <p>
      协作按原分支顺序等待，最多2个深度1任务；每任务6次总尝试、80秒仍受原请求90秒截止约束。single与协作使用相同事实守卫；每任务额外的委托与回收会增加模型调用，应结合固定内容对照选择。默认single。
    </p>
    <p v-if="!receipts?.length">
      此运行未记录专业任务；缺槽、单 Agent
      路径或旧运行均可能没有委托，不能从流程定义推断实际协作。
    </p>
    <article
      v-for="r in receipts || []"
      :key="r.task.task_id"
      class="route-result"
    >
      <b>{{ r.task.coordinator }} → {{ r.task.role_id }} · {{ r.status }}</b>
      <p>
        {{ r.task.goal }} · 节点 {{ r.task.node_id }} · 角色版本
        {{ r.task.role_version }}
      </p>
      <p>
        父 run {{ r.task.parent_run_id }} · 问题
        {{ r.task.parent_question_id }} / 输入版本 {{ r.task.question_version }}
      </p>
      <p>
        允许工具 {{ r.task.allowed_tools.join("、") }} · 实际 call
        {{ r.observation?.call_id ?? "未取得" }}
      </p>
      <p>
        回收证据
        {{ r.evidence_ids.join("、") || "尚无已回收证据，不能声称子任务完成" }}
      </p>
      <p>
        共享预算引用 {{ r.task.budget_ref }} · {{ r.started_at }} →
        {{ r.finished_at }}
      </p>
      <p v-if="r.error">{{ r.error.code }} · {{ r.error.message }}</p>
      <p v-for="d in r.decisions" :key="d.call_id">
        实际模型步骤 {{ d.phase }} · {{ d.model }} · call {{ d.call_id }}
      </p>
      <RawDetails title="原任务、槽位来源、版本和回执" :value="r" />
    </article>
    <h4 v-if="records?.length">原账本的子任务进度</h4>
    <article
      v-for="r in records || []"
      :key="r.task.task_id"
      class="route-result"
    >
      <b>{{ r.task.role_id }} · {{ r.state }} · {{ r.phase }}</b>
      <p>
        父 run {{ r.task.parent_run_id }} · 更新 {{ r.updated_at }} · 来源
        {{ source }}
      </p>
      <p>
        实际工具 call {{ r.observation?.call_id ?? "尚无已确认观察" }} ·
        已保存决策 {{ r.decisions.length }}
      </p>
      <p>
        COMPLETED 可读取原回执。RUNNING / OBSERVED / UNKNOWN
        保持待核对；取消不重发工具，部分完成不代表整次成功。
      </p>
      <RawDetails title="原任务的持久进度" :value="r" />
    </article>
    <p v-if="source === 'unavailable'">
      任务账本暂不可读，状态保持未知；刷新只读，不恢复调用。
    </p>
    <p>
      失败或取消不等于所有调用都未发送；用原 call
      和账本核对。没有回执时保持未知，浏览不恢复旧任务或批准写入。
    </p>
    <a
      href="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/09-agent-basics.md#受控专业-agent委托m40"
      target="_blank"
      rel="noopener"
      >课程：任务上下文、共享预算与原核心实验</a
    >
  </section>
</template>
