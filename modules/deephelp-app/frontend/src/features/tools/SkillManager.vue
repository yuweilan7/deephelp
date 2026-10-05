<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api } from "../../api/client";
import { useRead, useAction } from "../../composables/requests";
import { workspace, connect } from "../../composables/workspace";
import { newMessage, uncertain } from "../../composables/message";
import type { RunResponse } from "../../api/types";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
type Entry = {
  skill_id: string;
  version: string;
  goal: string;
  applicable_intents: string[];
};
type Candidate = {
  candidate_digest: string;
  revision: number;
  status: string;
  registry: unknown;
};
type Catalog = {
  published: { definitions: Entry[]; compositions: Entry[] };
  published_digest: string;
  candidates: Candidate[];
};
const catalog = useRead<Catalog>(),
  action = useAction();
const difference = useRead<unknown>();
const leaf = ref("order-benefits"),
  version = ref("2"),
  goal = ref("");
const reason = ref(""),
  confirmed = ref(false),
  order = ref(""),
  coupon = ref("");
function refresh() {
  return catalog.load("/learning/skills");
}
onMounted(refresh);
function draft() {
  const base = catalog.data.value;
  if (!base) return;
  void action.run(async () => {
    await api.request("/learning/skills/candidates", "POST", {
      base_digest: base.published_digest,
      skill_id: leaf.value,
      version: version.value,
      goal: goal.value,
      confirmed: true,
    });
    await refresh();
  });
}
function review(candidate: Candidate, approve: boolean) {
  if (!confirmed.value || !reason.value.trim()) return;
  void action.run(async () => {
    await api.request(
      `/learning/skills/${candidate.candidate_digest}/review`,
      "POST",
      {
        revision: candidate.revision,
        approve,
        reason: reason.value,
        confirmed: true,
      },
    );
    confirmed.value = false;
    await refresh();
  });
}
function trial() {
  if (!order.value || workspace.pending || workspace.sending) return;
  void action.run(async () => {
    const text = coupon.value
      ? `订单${order.value}的优惠券${coupon.value}不能用，请核对订单实付与券门槛。`
      : `查询订单${order.value}参加的活动。`;
    const body = newMessage(crypto.randomUUID(), text, null);
    workspace.pending = body;
    workspace.sending = true;
    try {
      const response = await api.request<RunResponse>(
        "/converse",
        "POST",
        body,
      );
      workspace.session = body.session_id;
      workspace.selected = { session_id: body.session_id, response };
      workspace.pending = null;
      await connect();
    } catch (e) {
      if (!uncertain(e)) workspace.pending = null;
      throw e;
    } finally {
      workspace.sending = false;
    }
  });
}
</script>
<template>
  <section class="card">
    <h3>Skill 详情、候选与试运行</h3>
    <p>
      选择按当前意图与工具许可确定。浏览、版本差异和审核新增模型0次；编辑形成候选，审核后仍需原完整内容验证与发布。候选不会热替换正在执行的版本。
    </p>
    <StateNotice
      :error="catalog.error.value"
      :loading="catalog.loading.value"
    />
    <el-button @click="refresh">读取当前版本 · 0 模型</el-button>
    <template v-if="catalog.data.value">
      <div
        v-for="entry in [
          ...catalog.data.value.published.definitions,
          ...(catalog.data.value.published.compositions || []),
        ]"
        :key="entry.skill_id"
        class="route-result"
      >
        <b>{{ entry.skill_id }} / {{ entry.version }}</b>
        <p>{{ entry.goal }} · 适用 {{ entry.applicable_intents.join("、") }}</p>
      </div>
      <RawDetails
        title="当前精确机器契约、schema、依赖与许可"
        :value="catalog.data.value.published"
      />
      <form @submit.prevent="draft">
        <label
          >登记叶子<select v-model="leaf">
            <option
              v-for="d in catalog.data.value.published.definitions"
              :key="d.skill_id"
              :value="d.skill_id"
            >
              {{ d.skill_id }}
            </option>
          </select></label
        >
        <label
          >新版本<input
            v-model="version"
            required
            pattern="[A-Za-z0-9][A-Za-z0-9.-]{0,31}"
        /></label>
        <label
          >候选目标与说明<textarea v-model="goal" required maxlength="2000" />
        </label>
        <el-button native-type="submit" :disabled="action.busy.value"
          >生成新候选 · 不发布</el-button
        >
      </form>
      <label>审核理由<textarea v-model="reason" maxlength="1000" /></label>
      <el-checkbox v-model="confirmed"
        >已读取版本差异、schema及依赖，明确审核此候选</el-checkbox
      >
      <div
        v-for="c in catalog.data.value.candidates"
        :key="c.candidate_digest"
        class="route-result"
      >
        <p>
          {{ c.candidate_digest }} · revision {{ c.revision }} · {{ c.status }}
        </p>
        <el-button
          @click="
            difference.load(`/learning/skills/${c.candidate_digest}/diff`)
          "
          >读取版本差异</el-button
        >
        <el-button
          :disabled="
            !confirmed ||
            !reason.trim() ||
            c.status !== 'CANDIDATE' ||
            action.busy.value
          "
          @click="review(c, true)"
          >审核通过 · 不激活</el-button
        >
        <el-button
          :disabled="
            !confirmed ||
            !reason.trim() ||
            c.status !== 'CANDIDATE' ||
            action.busy.value
          "
          @click="review(c, false)"
          >拒绝候选</el-button
        >
      </div>
      <StateNotice
        :error="difference.error.value"
        :loading="difference.loading.value"
      />
      <RawDetails
        v-if="difference.data.value"
        title="原版本与候选的实际差异"
        :value="difference.data.value"
      />
      <h4>明确试运行当前发布版本</h4>
      <p>
        填写你可访问的订单；券编号可选。将创建新的原业务会话，按实际主链调用模型和MCP，消耗页面显示的原累计预算。下方回执和对话页显示实际结果；缺槽、拒绝、失败或未知按原记录呈现。候选离线试运行见课程命令，不能据此宣称真实模型验证。
      </p>
      <label>订单编号<input v-model="order" maxlength="128" /></label>
      <label>券编号<input v-model="coupon" maxlength="128" /></label>
      <el-button
        :disabled="
          !order ||
          action.busy.value ||
          workspace.sending ||
          !!workspace.pending
        "
        @click="trial"
        >明确执行当前 Skill 业务试运行</el-button
      >
    </template>
    <StateNotice :error="action.error.value" :loading="action.busy.value" />
  </section>
</template>
