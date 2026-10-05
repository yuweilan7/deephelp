<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { api } from "../../api/client";
import type { LearningStatus, Operation, DemoResult } from "../../api/learning";
import { useRead, useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
import MechanismGuide from "../../components/MechanismGuide.vue";
const status = useRead<LearningStatus>(),
  workflow = useRead<{
    approval_graph?: { edges: { source: string; target: string }[] };
  }>(),
  action = useAction();
const operation = ref<Operation | null>(null),
  result = ref<DemoResult | null>(null),
  opid = ref(""),
  session = ref(""),
  run = ref(""),
  confirmed = ref(false);
const bound = computed(
  () =>
    operation.value?.plan.operation_id === opid.value &&
    operation.value.session_id === session.value &&
    operation.value.run_id === run.value,
);
onMounted(() => status.load("/learning/status"));
function bind(op: Operation) {
  operation.value = op;
  opid.value = op.plan.operation_id;
  session.value = op.session_id;
  run.value = op.run_id;
  confirmed.value = false;
}
async function read() {
  const binding = { id: opid.value, sid: session.value, rid: run.value };
  const d = await api.request<DemoResult>(
    `/learning/demo/${encodeURIComponent(binding.id)}?session_id=${encodeURIComponent(binding.sid)}&run_id=${encodeURIComponent(binding.rid)}`,
  );
  if (
    binding.id === opid.value &&
    binding.sid === session.value &&
    binding.rid === run.value
  ) {
    result.value = d;
    bind(d.operation);
  }
}
function create() {
  void action.run(async () => {
    const op = await api.request<Operation>("/learning/demo", "POST");
    bind(op);
    await read();
    await status.load("/learning/status");
  });
}
function decide(decision: string) {
  if (!bound.value || !confirmed.value) return;
  void action.run(async () => {
    const op = operation.value!;
    await api.request(
      `/learning/demo/${encodeURIComponent(op.plan.operation_id)}/approval`,
      "POST",
      {
        session_id: op.session_id,
        run_id: op.run_id,
        expected_question_version: op.question_version,
        parameters_hash: op.plan.parameters_hash,
        sop_version: op.plan.sop_version,
        snapshot_hash: op.plan.snapshot_hash,
        decision,
      },
    );
    confirmed.value = false;
    await read();
  });
}
function resume() {
  if (!bound.value) return;
  void action.run(async () => {
    const op = operation.value!;
    await api.request(
      `/learning/demo/${encodeURIComponent(op.plan.operation_id)}/resume`,
      "POST",
      { session_id: op.session_id, run_id: op.run_id },
    );
    await read();
  });
}
</script>
<template>
  <section class="card">
    <h2>SOP 与实际审批图</h2>
    <MechanismGuide mechanism="workflow" />
    <p>
      普通主链复用原 SOP 执行器，审批部分使用 LangGraph。PG
      操作账本是效果权威，checkpoint 记录图进度。
    </p>
    <el-button @click="workflow.load('/learning/workflow')"
      >读取 SOP 与实际图 · 0 模型</el-button
    ><StateNotice
      :error="workflow.error.value"
      :loading="workflow.loading.value"
    />
    <p
      v-for="(edge, i) in workflow.data.value?.approval_graph?.edges || []"
      :key="i"
    >
      {{ edge.source }} → {{ edge.target }}
    </p>
    <RawDetails v-if="workflow.data.value" :value="workflow.data.value" />
    <p>
      上方是已装配图的定义，只有原checkpoint/next/interrupt能证明此操作实际走过或暂停在哪个节点。整条13段主链并非都由LangGraph运行。
    </p>
    <h3>独立合成权益练习</h3>
    <StateNotice :error="status.error.value" :loading="status.loading.value" />
    <p v-if="status.data.value">
      {{
        status.data.value.demo_enabled
          ? "固定 DEMO-D01 / stdio MCP / PG / 合成权益 HTTP 已启用。"
          : `当前个人身份不能创建该独立练习；它属于 ${status.data.value.demo_identity_required}，服务端不会转移归属。`
      }}
    </p>
    <p>
      合成动作新增远程模型调用 0
      次，不表示真实退款。先核对原绑定和版本再明确审批；普通消息不批准操作，结果未知先读取原操作对账。
    </p>
    <el-button
      :disabled="!status.data.value?.demo_enabled || action.busy.value"
      @click="create"
      >创建合成待审批练习</el-button
    >
    <form @submit.prevent="action.run(read)">
      <label for="demo-id">原操作编号</label
      ><input
        id="demo-id"
        v-model="opid"
        required
        @input="confirmed = false"
      /><label for="demo-session">原会话编号</label
      ><input
        id="demo-session"
        v-model="session"
        required
        @input="confirmed = false"
      /><label for="demo-run">原 run 编号</label
      ><input
        id="demo-run"
        v-model="run"
        required
        @input="confirmed = false"
      /><el-button native-type="submit" :loading="action.busy.value"
        >按原绑定读取</el-button
      >
    </form>
    <StateNotice :error="action.error.value" /><template v-if="result"
      ><p>
        实际 {{ result.operation.status }} · 审批
        {{ result.operation.approval_status }} · 持久效果 {{ result.effects }} ·
        派发 {{ result.operation.dispatch_attempts }} · 对账
        {{ result.operation.query_attempts }} · checkpoint
        {{ result.checkpoints.length }} · 下一节点
        {{ result.next.join(", ") || "已结束" }}
      </p>
      <RawDetails
        title="核对原操作 / 参数 / 版本 / 快照 / interrupt"
        :value="result"
      />
      <p v-if="result.operation.status === 'UNKNOWN'">
        效果未知：不能显示“失败且无效果”。明确恢复先query原operation；SUCCESS采用原效果，ABSENT才允许派发，查询不可用继续保留UNKNOWN。
      </p>
      <p>
        thread绑定原operation，checkpoint
        ID是这次保存进度；parent指向前一个保存点，pending
        writes是任务已保存的频道写入，不是退款账本。人工等待不计入派发耗时；恢复新trace通过原前序关联。
      </p>
      <div v-for="c in result.checkpoints" :key="c.id" class="route-result">
        <b>保存点 {{ c.id }}</b>
        <p>待写任务 {{ c.pending_writes.length }} 条</p>
        <RawDetails
          title="父保存点与任务频道"
          :value="{
            parent: c.parent,
            pending_writes: c.pending_writes,
            metadata: c.metadata,
          }"
        />
      </div> </template
    ><el-checkbox v-model="confirmed" :disabled="!bound"
      >已核对显示的原绑定与快照，独立确认本次审批</el-checkbox
    >
    <div class="actions">
      <el-button
        :disabled="
          !bound ||
          !confirmed ||
          operation?.approval_status !== 'PENDING' ||
          action.busy.value
        "
        @click="decide('approve')"
        >明确批准</el-button
      ><el-button
        :disabled="
          !bound ||
          !confirmed ||
          operation?.approval_status !== 'PENDING' ||
          action.busy.value
        "
        @click="decide('reject')"
        >拒绝</el-button
      ><el-button
        :disabled="
          !bound ||
          !confirmed ||
          operation?.approval_status !== 'APPROVED' ||
          ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(
            operation?.status || '',
          ) ||
          action.busy.value
        "
        @click="decide('revoke')"
        >撤销批准</el-button
      ><el-button :disabled="!bound || action.busy.value" @click="resume"
        >恢复 / 核对原操作</el-button
      >
    </div>
  </section>
</template>
