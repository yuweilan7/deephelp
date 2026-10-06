<script setup lang="ts">
import { ref, shallowRef, computed, watch, onMounted } from "vue";
import { api } from "../../api/client";
import {
  fields,
  editorValues,
  recordBody,
  updateBody,
  type BusinessKind,
  type BusinessRecord,
} from "../../api/business";
import type { Page } from "../../api/types";
import { workspace } from "../../composables/workspace";
import { useRead, useAction } from "../../composables/requests";
import PageGuide from "../../components/PageGuide.vue";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const kind = ref<BusinessKind>("orders"),
  query = ref(""),
  selected = shallowRef<BusinessRecord | null>(null);
const values = ref(editorValues("orders", null)),
  relations = ref(""),
  relationsReady = ref(false),
  saved = ref<unknown>(null);
const list = useRead<Page<BusinessRecord>>(),
  action = useAction();
const labels = { orders: "订单", coupons: "优惠券", activities: "活动", eligibilities: "活动资格与名额" };
const rows = computed(
  () =>
    list.data.value?.items.filter((r) =>
      String(r.data[fields[kind.value][0].key]).includes(query.value),
    ) || [],
);
function edit(record: BusinessRecord | null = null) {
  selected.value = record;
  values.value = editorValues(kind.value, record);
  relations.value = "";
  relationsReady.value = false;
  saved.value = null;
  action.notice.value = "";
}
async function load(offset = 0) {
  await list.load(`/business/${kind.value}?offset=${offset}`);
}
async function select(record: BusinessRecord) {
  edit(record);
  action.error.value = "";
  const selection = record;
  if (kind.value === "orders") {
    const r = await api
      .request<{
        activity_ids: string[];
      }>(
        `/business/orders/${encodeURIComponent(String(record.data.order_id))}/activities`,
      )
      .catch((e) => {
        if (selected.value === selection) action.error.value = e.message;
        return null;
      });
    if (r && selected.value === selection) {
      relations.value = r.activity_ids.join(",");
      relationsReady.value = true;
    }
  }
}
watch(kind, () => {
  edit();
  void load();
});
onMounted(() => load());
function save() {
  void action.run(async () => {
    const current = kind.value,
      old = selected.value,
      data = recordBody(current, values.value);
    const id = String(data[fields[current][0].key]);
    const r = await api.request<BusinessRecord>(
      `/business/${current}${old ? "/" + encodeURIComponent(id) : ""}`,
      old ? "PUT" : "POST",
      old ? updateBody(old, data) : data,
    );
    if (current === kind.value) {
      await select(r);
      saved.value = r;
      await load();
      action.notice.value =
        "资料已保存；新消息读取新版本，原 receipt 保留旧事实。";
    }
  });
}
function saveRelations() {
  if (!relationsReady.value) return;
  void action.run(async () => {
    const old = selected.value!;
    await api.request(
      `/business/orders/${encodeURIComponent(String(old.data.order_id))}/activities`,
      "PUT",
      {
        expected_version: old.version,
        activity_ids: relations.value
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      },
    );
    if (kind.value === "orders" && selected.value === old) {
      const record = await api.request<BusinessRecord>(
        `/business/orders/${encodeURIComponent(String(old.data.order_id))}`,
      );
      await select(record);
      saved.value = record;
      await load();
      action.notice.value = "关系已保存，订单版本已更新。";
    }
  });
}
</script>
<template>
  <PageGuide
    title="业务资料"
    goal="维护个人持久事实，让只读工具返回可核对的当前版本。保存资料不调用模型。"
    needs="服务端已启用 Business MCP 与 PostgreSQL"
    :steps="[
      '选择资料类型并检索编号',
      '新建或选择当前资料',
      '保存后核对返回版本',
      '回到对话，用新消息查询；旧消息重投仍返回旧事实',
    ]"
    expected="服务端确认保存才显示新版本。旧版本 CAS 冲突会拒绝；活动资格须先维护同主体活动，unknown名额与0名额含义不同。新目录未发布时对话不可执行新标签。"
    lesson="https://github.com/yuweilan7/deephelp/blob/main/docs/learning/08-message-path.md"
  />
  <div class="two-column">
    <section class="card">
      <h2>选择资料</h2>
      <label for="business-kind">资料类型</label
      ><select id="business-kind" v-model="kind" :disabled="action.busy.value">
        <option v-for="(label, key) in labels" :key="key" :value="key">
          {{ label }}
        </option></select
      ><el-input
        v-model="query"
        aria-label="检索已读取资料"
        placeholder="检索资料编号"
      />
      <div class="actions">
        <el-button @click="load()">读取列表</el-button
        ><el-button :disabled="action.busy.value" @click="edit()"
          >新建资料</el-button
        >
      </div>
      <StateNotice
        :error="list.error.value"
        :loading="list.loading.value"
        :empty="!list.loading.value && !rows.length"
        next="当前页无匹配资料，请新建、清除筛选或读取下一页。"
      /><button
        v-for="r in rows"
        :key="String(r.data[fields[kind][0].key])"
        class="session-row"
        :aria-current="
          selected?.data[fields[kind][0].key] === r.data[fields[kind][0].key]
        "
        :disabled="action.busy.value"
        @click="select(r)"
      >
        {{ r.data[fields[kind][0].key] }} · 版本 {{ r.version }}</button
      ><el-button
        v-if="list.data.value?.next_offset != null"
        @click="load(list.data.value.next_offset)"
        >下一页资料</el-button
      >
    </section>
    <section class="card">
      <h2>
        {{ selected ? "编辑" : "新建" }}{{ labels[kind]
        }}{{ selected ? ` · 版本 ${selected.version}` : "" }}
      </h2>
      <StateNotice :error="action.error.value" />
      <p v-if="!workspace.connection?.execution.business_maintenance">
        当前服务未启用业务维护，按运行说明配置 Business 服务。
      </p>
      <form @submit.prevent="save">
        <template v-for="f in fields[kind]" :key="f.key"
          ><label :for="'business-' + f.key">{{ f.label }}</label
          ><select
            v-if="f.options"
            :id="'business-' + f.key"
            v-model="values[f.key]"
            :disabled="action.busy.value"
          >
            <option v-for="o in f.options" :key="o">{{ o }}</option></select
          ><input
            v-else
            :id="'business-' + f.key"
            v-model="values[f.key]"
            required
            :readonly="!!selected && f.key === fields[kind][0].key"
            :disabled="action.busy.value" /></template
        ><el-button
          type="primary"
          native-type="submit"
          :loading="action.busy.value"
          :disabled="!workspace.connection?.execution.business_maintenance"
          >保存资料 · 0 模型</el-button
        >
      </form>
      <form
        v-if="selected && kind === 'orders'"
        @submit.prevent="saveRelations"
      >
        <label for="activity-ids">订单活动关系（逗号分隔）</label
        ><input
          id="activity-ids"
          v-model="relations"
          :disabled="action.busy.value"
        /><el-button
          native-type="submit"
          :disabled="action.busy.value || !relationsReady"
          >按当前订单版本保存关系</el-button
        >
      </form>
      <p role="status">{{ action.notice.value }}</p>
      <RawDetails
        :value="saved || selected"
        title="实际保存结果 / 当前选中原记录"
      />
    </section>
  </div>
</template>
