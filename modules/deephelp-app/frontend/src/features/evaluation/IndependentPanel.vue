<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../../api/client";
import { useAction, useRead } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
interface Dataset {
  dataset_id: string;
  revision: number;
  status: string;
  source_kind: string;
  author_id: string;
  cases: number;
  turns: number;
  split_counts: Record<string, number>;
  reviewers: string[];
  disagreements: number;
  unresolved: number;
  freeze_hash: string | null;
}
const base = "/learning/evaluation/independent";
const data = useRead<{ status: string; datasets: Dataset[] }>();
const action = useAction(),
  payload = ref(""),
  selected = ref<Dataset | null>(null);
const operation = ref("annotations");
async function refresh() {
  await data.load(base, true);
}
async function submit() {
  await action.run(async () => {
    const body = JSON.parse(payload.value);
    const path =
      operation.value === "datasets"
        ? `${base}/datasets`
        : `${base}/datasets/${selected.value?.dataset_id}/${operation.value}`;
    const result = await api.request<Dataset>(
      path,
      "POST",
      operation.value === "datasets"
        ? body
        : { revision: selected.value?.revision, ...body },
    );
    selected.value = result;
    action.notice.value =
      "已保存回执；响应未知时先刷新，核对 revision 后再操作。";
    await refresh();
  });
}
onMounted(refresh);
</script>
<template>
  <el-card
    ><h3>独立数据与盲审</h3>
    <p>
      来源分为获准客户资料、独立人类合成和独立 AI
      合成。两份盲标须只看输入，分歧由第三位主体复核；换编号不增加独立样本数。冻结后不可改金标，浏览与审核
      0 模型。
    </p>
    <el-button @click="refresh">读取来源与锁定状态 · 0 模型</el-button>
    <StateNotice
      :error="data.error.value || action.error.value"
      :loading="data.loading.value || action.busy.value"
      :notice="action.notice.value"
    />
    <p v-if="data.data.value?.status === 'DATA_NOT_AVAILABLE'">
      DATA_NOT_AVAILABLE：尚无独立来源，不能以原同作者自测宣称 M45 完成。
    </p>
    <div v-for="row in data.data.value?.datasets" :key="row.dataset_id">
      <el-button @click="selected = row"
        >选择 {{ row.dataset_id.slice(0, 12) }} · {{ row.status }}</el-button
      >
      <p>
        {{ row.source_kind }} / 作者 {{ row.author_id }} · {{ row.cases }} 会话
        / {{ row.turns }} 消息 · {{ JSON.stringify(row.split_counts) }} ·
        {{ row.reviewers.length }} 位盲标者 · 分歧 {{ row.disagreements }} /
        未决 {{ row.unresolved }} · revision {{ row.revision }}
      </p>
      <RawDetails title="来源、冻结 hash 与真实计数" :value="row" />
    </div>
    <details>
      <summary>接收脱敏输入 / 提交独立收据 / 锁定审核集</summary>
      <p>
        请选择独立来源及完整盲标收据；输入不含答案。作者不得自审。导入主体和声明
        reviewer
        分开记录，声明不能证明人类独立性。锁定之后要重新开发须另取独立留出集。
      </p>
      <el-select v-model="operation"
        ><el-option label="接收脱敏输入" value="datasets" /><el-option
          label="提交完整盲标"
          value="annotations" /><el-option
          label="分歧第三方复核"
          value="adjudication" /><el-option label="不可变冻结" value="freeze"
      /></el-select>
      <el-input
        v-model="payload"
        type="textarea"
        :rows="5"
        placeholder="粘贴有来源声明的接收/盲标 JSON；冻结使用 {}"
      />
      <el-button
        :disabled="action.busy.value || (operation !== 'datasets' && !selected)"
        @click="submit"
        >提交明确操作 · 0 模型</el-button
      >
    </details>
  </el-card>
</template>
