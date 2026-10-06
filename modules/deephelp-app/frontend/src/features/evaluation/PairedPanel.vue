<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../../api/client";
import { useAction, useRead } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
interface Experiment {
  id: string;
  fingerprint: string;
  split: string;
  target: string;
  repeats: number;
  factor: string;
  freeze_hash: string;
  source: Record<string, string>;
  report: unknown;
}
const emit = defineEmits<{ task: [id: string] }>();
const data = useRead<{ experiments: Experiment[]; model_calls: number }>();
const reviews = useRead<{ improvements: unknown[]; model_calls: number }>();
const action = useAction(),
  confirmed = ref(false),
  selected = ref("");
async function refresh() {
  await Promise.all([
    data.load("/learning/evaluation/independent-experiments", true),
    reviews.load("/learning/evaluation/quality-improvements", true),
  ]);
}
async function start() {
  await action.run(async () => {
    const result = await api.request<{ id: string }>(
      "/learning/evaluation/tasks",
      "POST",
      {
        action: "independent_pair",
        experiment_id: selected.value,
        confirmed: true,
      },
    );
    emit("task", result.id);
    action.notice.value =
      "已交给原任务队列；等待、取消、失败和恢复见右侧任务。执行完成不等于质量收益。";
  });
}
onMounted(refresh);
</script>
<template>
  <el-card>
    <h3>独立版本配对 · AI 合成</h3>
    <p>
      同一冻结样本、模型与语料，一次预设重复，逐例交替先后顺序。首条消息分类不等于业务解决率；小样本不能外推客户收益。读取结果
      0 模型。
    </p>
    <el-button @click="refresh">刷新合同与实际报告</el-button>
    <p>
      一轮改进按开发差例→事前标准与审查身份→唯一候选→锁定留出集→采用资格或拒绝保存。AI
      开发审查明确披露；采用资格仍需原完整内容、审核、安装与恢复门禁。
    </p>
    <RawDetails
      title="原 PG 改进证据链 / 事前标准 / 采用或拒绝原因"
      :value="reviews.data.value"
    />
    <StateNotice
      :error="reviews.error.value"
      :loading="reviews.loading.value"
    />
    <StateNotice
      :loading="data.loading.value || action.busy.value"
      :error="data.error.value || action.error.value"
      :notice="action.notice.value"
    />
    <p v-if="data.data.value?.experiments.length === 0">
      服务端尚未登记冻结实验；不会自动选择数据、版本或调用模型。
    </p>
    <div v-for="item in data.data.value?.experiments" :key="item.id">
      <p>
        {{ item.id }} · {{ item.split }} · {{ item.target }} ·
        {{ item.factor }} · 重复 {{ item.repeats }}
      </p>
      <RawDetails
        title="版本 / 数据 hash / 完整分母、混淆、差例与用量"
        :value="item"
      />
    </div>
    <el-select v-model="selected" placeholder="选择服务端冻结合同">
      <el-option
        v-for="item in data.data.value?.experiments"
        :key="item.id"
        :value="item.id"
        :label="`${item.id} / ${item.split}`"
      />
    </el-select>
    <el-checkbox v-model="confirmed"
      >已核对冻结范围与累计预算，明确执行真实配对</el-checkbox
    >
    <el-button
      :disabled="!confirmed || !selected || action.busy.value"
      @click="start"
      >启动原队列任务</el-button
    >
  </el-card>
</template>
