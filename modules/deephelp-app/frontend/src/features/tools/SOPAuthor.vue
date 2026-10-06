<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { api } from "../../api/client";
import { useAction, useRead } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
import SOPRehearsal from "./SOPRehearsal.vue";
type Condition = {
  fact: string;
  op: string;
  value: string | boolean | null;
  other_fact: string | null;
};
type Node = {
  node_id: string;
  kind: string;
  tool?: string | null;
  success?: string | null;
  failure?: string | null;
  default?: string | null;
  routes: { condition: Condition; target: string }[];
  status?: string | null;
  conclusion?: string | null;
  proposal?: string | null;
};
type Flow = {
  sop_id: string;
  version: string;
  intent_code: string;
  entry: string;
  required_slots: string[];
  allowed_tools: string[];
  nodes: Node[];
};
type Draft = {
  draft_id: string;
  revision: number;
  status: string;
  base_hash: string;
  content_hash: string;
  content: Flow;
  validation: {
    valid: boolean;
    errors: { path: (string | number)[]; message: string }[];
  };
};
type Catalog = {
  published: { definitions: Flow[] };
  base_hash: string;
  drafts: Draft[];
  facts: Record<string, Record<string, string>>;
  grants: Record<string, string[]>;
  live_readonly_available: boolean;
};
const catalog = useRead<Catalog>(),
  action = useAction();
const selected = ref(""),
  draft = ref<Draft | null>(null),
  content = ref<Flow | null>(null),
  dirty = ref(false),
  newKind = ref("end");
const facts = computed(
  () =>
    content.value?.nodes
      .filter((n) => n.kind === "lookup" && n.tool)
      .flatMap((n) =>
        Object.keys(catalog.data.value?.facts[n.tool!] || {}).map(
          (f) => `${n.node_id}.${f}`,
        ),
      ) || [],
);
const ids = computed(() => content.value?.nodes.map((n) => n.node_id) || []);
function refresh() {
  return catalog.load("/learning/sop");
}
onMounted(refresh);
function adopt(value: Draft) {
  draft.value = value;
  content.value = structuredClone(value.content);
  dirty.value = false;
}
function create() {
  void action.run(async () => {
    const base = catalog.data.value!;
    adopt(
      await api.request<Draft>("/learning/sop/drafts", "POST", {
        sop_id: selected.value || base.published.definitions[0]!.sop_id,
        base_hash: base.base_hash,
      }),
    );
    await refresh();
  });
}
function load(id: string) {
  void action.run(async () =>
    adopt(await api.request<Draft>(`/learning/sop/drafts/${id}`)),
  );
}
function save() {
  void action.run(async () => {
    const d = draft.value!;
    adopt(
      await api.request<Draft>(`/learning/sop/drafts/${d.draft_id}`, "PUT", {
        revision: d.revision,
        content: content.value,
      }),
    );
    await refresh();
  });
}
function add() {
  const flow = content.value!;
  const suffix = crypto.randomUUID().slice(0, 8),
    kind = newKind.value;
  flow.nodes.push({
    node_id: `${kind}-${suffix}`,
    kind,
    routes: [],
    ...(kind === "lookup"
      ? {
          tool: flow.allowed_tools[0],
          success: flow.entry,
          failure:
            flow.nodes.find(
              (n) =>
                n.kind === "end" &&
                ["FAILED", "HANDED_OFF"].includes(n.status || ""),
            )?.node_id || "",
        }
      : kind === "branch"
        ? {
            routes: [
              {
                condition: {
                  fact: facts.value[0] || "",
                  op: "eq",
                  value: "",
                  other_fact: null,
                },
                target: flow.entry,
              },
            ],
            default: flow.entry,
          }
        : { status: "HANDED_OFF", conclusion: "请补充终点的业务说明。" }),
  });
  dirty.value = true;
}
function operand(c: Condition) {
  c.value = c.op === "eq" ? "" : null;
  c.other_fact = ["fact_eq", "money_lt", "money_gte"].includes(c.op)
    ? facts.value[0] || ""
    : null;
}
</script>
<template>
  <section class="card">
    <h3>SOP 草稿编辑</h3>
    <p>
      从已发布流程创建草稿，修改查询、分支与终点。保存及校验新增模型0次；草稿不会改变正在运行的问题，也不批准退款或发券。
    </p>
    <StateNotice
      :error="catalog.error.value"
      :loading="catalog.loading.value"
    />
    <el-button @click="refresh">刷新流程与草稿 · 0 模型</el-button>
    <template v-if="catalog.data.value">
      <label
        >原流程<select v-model="selected">
          <option value="">选择首个流程</option>
          <option
            v-for="f in catalog.data.value.published.definitions"
            :key="f.sop_id"
            :value="f.sop_id"
          >
            {{ f.intent_code }} · {{ f.version }}
          </option>
        </select></label
      >
      <el-button :disabled="action.busy.value" @click="create"
        >创建独立草稿</el-button
      >
      <div class="actions">
        <el-button
          v-for="d in catalog.data.value.drafts"
          :key="d.draft_id"
          @click="load(d.draft_id)"
          >{{ d.content.intent_code }} · revision {{ d.revision }} ·
          {{ d.status }}</el-button
        >
      </div>
    </template>
    <template v-if="draft && content">
      <p>
        草稿 {{ draft.draft_id }} · revision {{ draft.revision }} ·
        {{ draft.status }} ·
        {{
          dirty
            ? "有未保存修改，校验结果对应上次保存"
            : draft.validation.valid
              ? "服务端结构校验通过"
              : "结构无效，请按错误位置修正"
        }}
      </p>
      <p>
        槽位
        {{ content.required_slots.join("、") }}
        直接映射原工具参数；主体来自服务端身份。工具许可由原意图限定，不能添加地址、脚本或任意参数。
      </p>
      <ul>
        <li v-for="(e, i) in draft.validation.errors" :key="i">
          {{ e.path.join(" → ") || "整个流程" }}：{{ e.message }}
        </li>
      </ul>
      <label
        >候选新版本<input v-model="content.version" @input="dirty = true"
      /></label>
      <label>本图的只读工具许可</label>
      <el-checkbox-group v-model="content.allowed_tools" @change="dirty = true">
        <el-checkbox
          v-for="t in catalog.data.value?.grants[content.intent_code] || []"
          :key="t"
          :value="t"
          >{{ t }}</el-checkbox
        >
      </el-checkbox-group>
      <label
        >入口节点<select v-model="content.entry" @change="dirty = true">
          <option v-for="id in ids" :key="id">{{ id }}</option>
        </select></label
      >
      <div
        v-for="(n, index) in content.nodes"
        :key="index"
        class="route-result"
        @input="dirty = true"
        @change="dirty = true"
      >
        <b
          >节点 {{ index + 1 }} ·
          {{
            n.kind === "lookup"
              ? "读取业务事实"
              : n.kind === "branch"
                ? "根据已验证事实分支"
                : "结束并说明结果"
          }}</b
        >
        <label>节点编号<input v-model="n.node_id" /></label>
        <template v-if="n.kind === 'lookup'">
          <label
            >只读工具<select v-model="n.tool">
              <option
                v-for="t in catalog.data.value?.grants[content.intent_code] ||
                []"
                :key="t"
              >
                {{ t }}
              </option>
            </select></label
          >
          <label
            >成功 →<select v-model="n.success">
              <option v-for="id in ids" :key="id">{{ id }}</option>
            </select></label
          >
          <label
            >失败 →<select v-model="n.failure">
              <option v-for="id in ids" :key="id">{{ id }}</option>
            </select></label
          >
        </template>
        <template v-if="n.kind === 'branch'">
          <p>按顺序检查有限条件，第一条匹配即选择目标；无匹配走默认分支。</p>
          <div v-for="(r, ri) in n.routes" :key="ri" class="card">
            <label
              >事实<select v-model="r.condition.fact">
                <option v-for="f in facts" :key="f">{{ f }}</option>
              </select></label
            >
            <label
              >比较<select
                v-model="r.condition.op"
                @change="operand(r.condition)"
              >
                <option value="eq">等于固定值</option>
                <option value="empty">文本为空</option>
                <option value="nonempty">文本非空</option>
                <option value="fact_eq">等于另一事实</option>
                <option value="money_lt">金额小于</option>
                <option value="money_gte">金额大于或等于</option>
              </select></label
            >
            <label v-if="r.condition.op === 'eq'"
              >固定值<input
                v-if="typeof r.condition.value !== 'boolean'"
                v-model="r.condition.value"
              /><select v-else v-model="r.condition.value">
                <option :value="true">true</option>
                <option :value="false">false</option></select
              ><el-button
                @click="
                  r.condition.value =
                    typeof r.condition.value === 'boolean' ? '' : true;
                  dirty = true;
                "
                >切换文本/布尔值</el-button
              ></label
            >
            <label v-if="r.condition.other_fact !== null"
              >另一事实<select v-model="r.condition.other_fact">
                <option v-for="f in facts" :key="f">{{ f }}</option>
              </select></label
            >
            <label
              >匹配 →<select v-model="r.target">
                <option v-for="id in ids" :key="id">{{ id }}</option>
              </select></label
            >
            <el-button
              @click="
                n.routes.splice(ri, 1);
                dirty = true;
              "
              >删除条件</el-button
            >
          </div>
          <el-button
            @click="
              n.routes.push({
                condition: {
                  fact: facts[0] || '',
                  op: 'eq',
                  value: '',
                  other_fact: null,
                },
                target: content.entry,
              });
              dirty = true;
            "
            >添加有限条件</el-button
          >
          <label
            >默认 →<select v-model="n.default">
              <option v-for="id in ids" :key="id">{{ id }}</option>
            </select></label
          >
        </template>
        <template v-if="n.kind === 'end'">
          <label
            >结果状态<select v-model="n.status">
              <option>RESOLVED</option>
              <option>HANDED_OFF</option>
              <option>FAILED</option>
              <option v-if="n.proposal">NEEDS_APPROVAL</option>
            </select></label
          >
          <label
            >业务结论<textarea v-model="n.conclusion" maxlength="1000" />
          </label>
          <p v-if="n.proposal">
            原审批计划 {{ n.proposal }} 仍须原审批服务；草稿不构成批准。
          </p>
        </template>
        <el-button
          :disabled="draft.status !== 'DRAFT'"
          @click="
            content.nodes.splice(index, 1);
            dirty = true;
          "
          >删除节点</el-button
        >
      </div>
      <select v-model="newKind">
        <option value="lookup">只读查询</option>
        <option value="branch">有限条件</option>
        <option value="end">终点</option>
      </select>
      <el-button :disabled="draft.status !== 'DRAFT'" @click="add"
        >添加节点</el-button
      >
      <el-button
        :disabled="action.busy.value || draft.status !== 'DRAFT'"
        @click="save"
        >保存并服务端校验</el-button
      >
      <el-button :disabled="action.busy.value" @click="load(draft.draft_id)"
        >重载已保存草稿</el-button
      >
      <RawDetails title="草稿契约、hash与服务端校验" :value="draft" />
      <SOPRehearsal
        :draft="draft"
        :dirty="dirty"
        :live-available="catalog.data.value?.live_readonly_available || false"
      />
    </template>
    <StateNotice :error="action.error.value" :loading="action.busy.value" />
  </section>
</template>
