<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../api/client";
import { useRead, useAction } from "../../composables/requests";
import StateNotice from "../../components/StateNotice.vue";
import RawDetails from "../../components/RawDetails.vue";
type Draft = {
  draft_id: string;
  revision: number;
  status: string;
  base_hash: string;
  validation: { valid: boolean };
  candidate_hash?: string;
  review_digest?: string;
};
const props = defineProps<{ draft: Draft; dirty: boolean }>(),
  emit = defineEmits<{ reviewed: [draft: Draft] }>();
const diff = useRead<{
    changes: { path: (string | number)[]; before: unknown; after: unknown }[];
    base_current: boolean;
    candidate_hash: string;
  }>(),
  action = useAction();
const confirmed = ref(false),
  reason = ref("");
watch(
  () => [props.draft.draft_id, props.draft.revision],
  () => {
    diff.clear();
    confirmed.value = false;
    reason.value = "";
  },
);
function read() {
  void diff.load(`/learning/sop/drafts/${props.draft.draft_id}/diff`);
}
function review(approve: boolean) {
  void action.run(async () => {
    const result = await api.request<Draft>(
      `/learning/sop/drafts/${props.draft.draft_id}/review`,
      "POST",
      {
        revision: props.draft.revision,
        base_hash: props.draft.base_hash,
        approve,
        reason: reason.value,
        confirmed: true,
      },
    );
    emit("reviewed", result);
    confirmed.value = false;
  });
}
</script>
<template>
  <section class="card">
    <h4>SOP 差异与明确审核</h4>
    <p>
      读取已保存图与原基准的字段/节点条件差异，0模型。明确审核会冻结完整候选，之后从当前正式版创建新草稿再修改。
    </p>
    <el-button :disabled="dirty || !draft.validation.valid" @click="read"
      >读取保存后的结构化差异 · 0模型</el-button
    >
    <template v-if="diff.data.value"
      ><p>
        基准仍当前：{{ diff.data.value.base_current }} · 完整候选
        {{ diff.data.value.candidate_hash }}
      </p>
      <ul>
        <li v-for="(c, i) in diff.data.value.changes" :key="i">
          <strong>{{ c.path.join(".") }}</strong
          >：{{ JSON.stringify(c.before) }} → {{ JSON.stringify(c.after) }}
        </li>
      </ul>
      <p v-if="!diff.data.value.changes.length">
        没有内容差异；审核需要明确新版本。
      </p></template
    >
    <template v-if="draft.status === 'DRAFT'"
      ><label>审核理由<textarea v-model="reason" maxlength="1000" /></label
      ><el-checkbox v-model="confirmed"
        >已核对保存后的候选差异，明确审核此次 revision</el-checkbox
      ><el-button
        :disabled="
          !confirmed ||
          !reason.trim() ||
          dirty ||
          !diff.data.value?.base_current ||
          action.busy.value
        "
        @click="review(true)"
        >批准并冻结 SOP 候选</el-button
      ><el-button
        :disabled="
          !confirmed ||
          !reason.trim() ||
          dirty ||
          !diff.data.value?.base_current ||
          action.busy.value
        "
        @click="review(false)"
        >拒绝此 SOP 候选</el-button
      ></template
    >
    <p v-else>
      审核状态 {{ draft.status }} · revision {{ draft.revision }} · 候选
      {{ draft.candidate_hash }}。审核不改变 active，也不批准业务效果。
    </p>
    <p>
      到“评测与改进”选择同一已审核 SOP、Skill
      和资产，验证真实内容并准备完整版本，再按原完整 hash / 当前 revision
      发布。旧问题、待审批及 UNKNOWN 继续原绑定。
    </p>
    <router-link to="/evaluation">打开原内容门禁与版本发布</router-link>
    <RawDetails
      v-if="draft.review_digest"
      title="不可变 SOP 审核身份"
      :value="{
        review_digest: draft.review_digest,
        candidate_hash: draft.candidate_hash,
      }"
    />
    <StateNotice
      :error="diff.error.value || action.error.value"
      :loading="diff.loading.value || action.busy.value"
    />
  </section>
</template>
