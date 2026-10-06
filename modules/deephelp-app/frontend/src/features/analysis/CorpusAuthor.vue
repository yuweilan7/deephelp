<script setup lang="ts">
import { ref } from "vue";
import { api } from "../../api/client";
import { useAction } from "../../composables/requests";
import StateNotice from "../../components/StateNotice.vue";
import RawDetails from "../../components/RawDetails.vue";

type RecordValue = {
  doc_id: string;
  content: string;
  intent_code: string;
  split: string;
  source_group: string;
  variant_group: string;
  synthetic: true;
  registry_version: string;
  metadata: Record<string, string>;
};
type Entry = {
  record: RecordValue;
  source: string;
  status: string;
  revision: number;
};
type Preview = {
  records: RecordValue[];
  preview_id: string;
  file_sha256: string;
  rows: number;
  status: string;
  errors: string[];
  imported_revision: number | null;
};
type Workspace = {
  revision: number;
  catalog_version: string;
  entries: Entry[];
  total: number;
  next_offset: number | null;
  labels: { code: string; label: string }[];
  previews: Preview[];
  reviews: unknown[];
};
const row = ref<Workspace | null>(null),
  offset = ref(0),
  selected = ref<string[]>([]);
const action = useAction(),
  editing = ref(false),
  source = ref("合成学习资料"),
  reason = ref("");
const record = ref<RecordValue>({
  doc_id: "",
  content: "",
  intent_code: "",
  split: "reference",
  source_group: "",
  variant_group: "",
  synthetic: true,
  registry_version: "complaints-v3",
  metadata: {},
});
const columns = ref("{}"),
  file = ref<File | null>(null),
  exported = ref("");
async function load(page = offset.value) {
  row.value = await api.request<Workspace>(
    `/learning/corpus?offset=${page}&limit=20`,
  );
  offset.value = page;
  selected.value = [];
}
function edit(entry?: Entry) {
  editing.value = !!entry;
  record.value = entry
    ? JSON.parse(JSON.stringify(entry.record))
    : {
        doc_id: "",
        content: "",
        intent_code: row.value?.labels[0]?.code || "",
        split: "reference",
        source_group: "",
        variant_group: "",
        synthetic: true,
        registry_version: row.value!.catalog_version,
        metadata: {},
      };
  source.value = entry?.source || "合成学习资料";
}
async function write(path: string, method: string, body: object) {
  await api.request(path, method, { revision: row.value!.revision, ...body });
  await load();
}
async function upload() {
  if (!file.value) throw new Error("先选择文件");
  if (file.value.size > 2 * 1024 * 1024) throw new Error("文件超过2MiB上限");
  const bytes = new Uint8Array(await file.value.arrayBuffer());
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const format = file.value.name.split(".").at(-1)?.toLowerCase();
  await write("/learning/corpus/previews", "POST", {
    format,
    data_base64: btoa(binary),
    columns: JSON.parse(columns.value),
  });
  file.value = null;
}
function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null;
}
async function exportDraft() {
  exported.value = (
    await api.request<{ jsonl: string }>("/learning/corpus/export")
  ).jsonl;
}
</script>
<template>
  <section class="panel">
    <h2>语料草稿与导入审核</h2>
    <p>
      浏览、预览、保存、审核和导出均零模型。草稿保存在
      PostgreSQL；叶子标签由当前目录限定。
      语料用于召回学习，不改变订单、退款或券事实。正式数据集与索引通过候选和完整发布另行切换。
    </p>
    <button :disabled="action.busy.value" @click="action.run(() => load(0))">
      读取语料工作区
    </button>
    <StateNotice :error="action.error.value" :loading="action.busy.value" />
    <template v-if="row">
      <p>
        目录 {{ row.catalog_version }} · revision {{ row.revision }} · 共
        {{ row.total }} 条。 冲突时重新读取再核对；修改记录后须重新审核。split
        与去重组是冻结评测的边界。
      </p>
      <table>
        <thead>
          <tr>
            <th>审核</th>
            <th>ID / 标签 / split</th>
            <th>文本与来源</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in row.entries" :key="entry.record.doc_id">
            <td>
              <input
                v-model="selected"
                type="checkbox"
                :value="entry.record.doc_id"
              />
            </td>
            <td>
              {{ entry.record.doc_id }}<br />{{ entry.record.intent_code }} /
              {{ entry.record.split }}
            </td>
            <td>{{ entry.record.content }}<br />来源：{{ entry.source }}</td>
            <td>{{ entry.status }} · r{{ entry.revision }}</td>
            <td>
              <button @click="edit(entry)">编辑</button
              ><button
                :disabled="action.busy.value"
                @click="
                  action.run(() =>
                    write(
                      '/learning/corpus/records/' + entry.record.doc_id,
                      'DELETE',
                      {},
                    ),
                  )
                "
              >
                删除草稿
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <button
        :disabled="offset === 0"
        @click="action.run(() => load(Math.max(0, offset - 20)))"
      >
        上一页
      </button>
      <button
        :disabled="row.next_offset === null"
        @click="action.run(() => load(row!.next_offset!))"
      >
        下一页
      </button>
      <button @click="edit()">新建候选</button>
      <fieldset>
        <legend>{{ editing ? "编辑候选" : "新增候选" }}</legend>
        <label>ID <input v-model="record.doc_id" :disabled="editing" /></label>
        <label
          >叶子标签
          <select v-model="record.intent_code">
            <option
              v-for="label in row.labels"
              :key="label.code"
              :value="label.code"
            >
              {{ label.label }} / {{ label.code }}
            </option>
          </select></label
        >
        <label
          >split
          <select v-model="record.split">
            <option
              v-for="split in [
                'reference',
                'train',
                'dev',
                'test',
                'regression',
              ]"
              :key="split"
            >
              {{ split }}
            </option>
          </select></label
        >
        <label>文本 <textarea v-model="record.content" /></label>
        <label>来源说明 <input v-model="source" /></label>
        <label>来源组 <input v-model="record.source_group" /></label>
        <label>近义去重组 <input v-model="record.variant_group" /></label>
        <button
          :disabled="action.busy.value"
          @click="
            action.run(async () => {
              await write(
                '/learning/corpus/records' +
                  (editing ? '/' + record.doc_id : ''),
                editing ? 'PUT' : 'POST',
                { record, source },
              );
              editing = false;
            })
          "
        >
          保存草稿
        </button>
      </fieldset>
      <label>人工审核理由 <input v-model="reason" /></label>
      <button
        :disabled="!selected.length || !reason.trim() || action.busy.value"
        @click="
          action.run(() =>
            write('/learning/corpus/reviews', 'POST', {
              doc_ids: selected,
              approve: true,
              reason,
              confirmed: true,
            }),
          )
        "
      >
        明确批准所选记录
      </button>
      <button
        :disabled="!selected.length || !reason.trim() || action.busy.value"
        @click="
          action.run(() =>
            write('/learning/corpus/reviews', 'POST', {
              doc_ids: selected,
              approve: false,
              reason,
              confirmed: true,
            }),
          )
        "
      >
        拒绝所选记录
      </button>
      <fieldset>
        <legend>JSONL / CSV / Excel 导入</legend>
        <p>
          最多2MiB /
          1000行。CSV可以用冻结字段名，或明确填写字段→表头映射；Excel必须填写字段→A/B列字母映射，所有值保存为文本，公式拒绝。JSONL使用原
          CorpusRecord 字段；来源说明可放 metadata.source，来源组必须填写。
        </p>
        <input type="file" accept=".jsonl,.csv,.xlsx" @change="chooseFile" />
        <label>列映射 JSON <textarea v-model="columns" /></label>
        <button
          :disabled="!file || action.busy.value"
          @click="action.run(upload)"
        >
          仅预览与校验
        </button>
        <article v-for="preview in row.previews" :key="preview.preview_id">
          <p>
            {{ preview.preview_id }} · {{ preview.rows }}行 ·
            {{ preview.status }} · 导入revision
            {{ preview.imported_revision ?? "未导入" }}
          </p>
          <p>原文件hash：{{ preview.file_sha256 }}</p>
          <ul v-if="preview.errors.length">
            <li v-for="error in preview.errors" :key="error">{{ error }}</li>
          </ul>
          <p v-else>全部行校验通过；导入后仍是待审核草稿。</p>
          <RawDetails
            v-if="preview.records.length"
            title="核对映射后的预览记录"
            :value="preview.records"
          />
          <button
            :disabled="
              !!preview.errors.length ||
              preview.status !== 'PREVIEW' ||
              action.busy.value
            "
            @click="
              action.run(() =>
                write('/learning/corpus/imports', 'POST', {
                  preview_id: preview.preview_id,
                  confirmed: true,
                }),
              )
            "
          >
            明确导入全部行
          </button>
          <button
            v-if="preview.status === 'PREVIEW'"
            :disabled="action.busy.value"
            @click="
              action.run(() =>
                write(
                  '/learning/corpus/previews/' + preview.preview_id,
                  'DELETE',
                  {},
                ),
              )
            "
          >
            移除未用预览
          </button>
        </article>
      </fieldset>
      <button :disabled="action.busy.value" @click="action.run(exportDraft)">
        导出当前草稿 JSONL
      </button>
      <textarea
        v-if="exported"
        v-model="exported"
        readonly
        aria-label="导出语料"
      />
      <RawDetails title="人工审核记录" :value="row.reviews" />
    </template>
  </section>
</template>
