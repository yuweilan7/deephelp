<script setup lang="ts">
import { ref } from "vue";
import { api } from "../../api/client";
import { canCancel, canResume, type Task } from "../../api/evaluation";
import { useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";

type Version = { version_hash: string; version: string; created_at: string };
type Route = {
  elapsed_ms: number;
  hits: {
    doc_id: string;
    content: string;
    intent_code: string;
    score_kind: string;
    raw_score: number;
    metadata: unknown;
  }[];
};
type Comparison = {
  query_vector_sha256: string;
  results: Record<
    string,
    {
      scope: unknown;
      rerank: { status: string; reason: string };
      route_elapsed_ms: Record<string, number>;
      routes: Record<string, Route>;
    }
  >;
};
const action = useAction(),
  versions = ref<Version[]>([]),
  selected = ref("");
const name = ref(""),
  confirmed = ref(false),
  snapshot = ref<unknown>(null);
const query = ref("优惠券满足门槛却无法抵扣"),
  taskId = ref(""),
  task = ref<
    (Task & { result?: Comparison; corpus_evidence?: unknown }) | null
  >(null);
async function load() {
  versions.value = (
    await api.request<{ versions: Version[] }>("/learning/corpus/versions")
  ).versions;
  if (!versions.value.some((v) => v.version_hash === selected.value))
    selected.value = versions.value[0]?.version_hash || "";
}
async function freeze() {
  const row = await api.request<{ revision: number }>("/learning/corpus");
  await api.request("/learning/corpus/versions", "POST", {
    revision: row.revision,
    version: name.value,
    confirmed: true,
  });
  confirmed.value = false;
  await load();
}
async function readTask() {
  task.value = await api.request(
    `/learning/evaluation/tasks/${encodeURIComponent(taskId.value)}`,
  );
}
async function readSnapshot() {
  snapshot.value = await api.request(
    "/learning/corpus/versions/" + selected.value,
  );
}
async function start(mode: "corpus_build" | "corpus_compare") {
  const row = await api.request<Task>("/learning/evaluation/tasks", "POST", {
    action: mode,
    corpus_version: selected.value,
    query: mode === "corpus_compare" ? query.value : null,
    confirmed: true,
  });
  taskId.value = row.id;
  confirmed.value = false;
  await readTask();
}
async function control(op: "cancel" | "resume") {
  await api.request(
    `/learning/evaluation/tasks/${encodeURIComponent(taskId.value)}/${op}`,
    "POST",
  );
  await readTask();
}
</script>
<template>
  <section class="panel">
    <h2>冻结候选、独立索引与同向量对照</h2>
    <p>
      所有记录须有与当前内容、来源和 revision
      匹配的人工审核。冻结、读取版本与任务均零模型；构建和比较需显式执行，沿评测工作台同一个重任务、累计预算和取消恢复入口。
    </p>
    <button :disabled="action.busy.value" @click="action.run(load)">
      读取冻结版本
    </button>
    <label for="corpus-version-name">新冻结版本名</label
    ><input id="corpus-version-name" v-model="name" maxlength="128" />
    <label
      ><input
        v-model="confirmed"
        type="checkbox"
      />确认本次冻结或模型操作</label
    >
    <button
      :disabled="!confirmed || !name || action.busy.value"
      @click="action.run(freeze)"
    >
      冻结已审核语料
    </button>
    <StateNotice :error="action.error.value" :loading="action.busy.value" />
    <label for="corpus-version">不可变候选版本</label>
    <select id="corpus-version" v-model="selected" @change="snapshot = null">
      <option
        v-for="v in versions"
        :key="v.version_hash"
        :value="v.version_hash"
      >
        {{ v.version }} · {{ v.version_hash }}
      </option>
    </select>
    <button
      :disabled="!selected || action.busy.value"
      @click="action.run(readSnapshot)"
    >
      读取审核与冻结来源
    </button>
    <RawDetails
      v-if="snapshot"
      title="冻结记录、split、来源 hash 与人工审核"
      :value="snapshot"
    />
    <p>
      构建只写候选专用集合和 pointer。正式 active / previous
      通过完整发布另行切换。独立 dev / test
      在模型调用前冻结；已确认向量及完成观察续跑复用，未知用量保持未知。默认未配置
      Rerank 时明确显示未运行。
    </p>
    <button
      :disabled="!selected || !confirmed || action.busy.value"
      @click="action.run(() => start('corpus_build'))"
    >
      显式构建候选索引与检索门禁
    </button>
    <label for="corpus-query">候选与正式索引共用查询</label
    ><textarea id="corpus-query" v-model="query" maxlength="2000" />
    <button
      :disabled="!selected || !query.trim() || !confirmed || action.busy.value"
      @click="action.run(() => start('corpus_compare'))"
    >
      执行同向量检索对照
    </button>
    <label for="corpus-task">原重任务 ID（重载后可继续读取）</label
    ><input id="corpus-task" v-model="taskId" />
    <button
      :disabled="!taskId || action.busy.value"
      @click="action.run(readTask)"
    >
      读取候选任务进度
    </button>
    <button
      :disabled="!canCancel(task) || action.busy.value"
      @click="action.run(() => control('cancel'))"
    >
      取消候选任务
    </button>
    <button
      :disabled="!canResume(task, 3) || action.busy.value"
      @click="action.run(() => control('resume'))"
    >
      恢复原候选任务
    </button>
    <p v-if="task">
      {{ task.state }} · attempt {{ task.attempt }} ·
      {{ task.error || task.result?.evidence_status || "等待实际结果" }}
    </p>
    <template v-if="task?.result?.results">
      <p>
        同一次实际查询向量 SHA256：{{
          task.result.query_vector_sha256
        }}。分数是召回证据，标签仍需最终分类守卫；语料不能生成订单或券事实。
      </p>
      <div v-for="(version, key) in task.result.results" :key="key">
        <h3>{{ key === "candidate" ? "候选索引" : "当前正式索引" }}</h3>
        <RawDetails title="真实集合与版本" :value="version.scope" />
        <p>Rerank：{{ version.rerank.status }} · {{ version.rerank.reason }}</p>
        <div v-for="(route, mode) in version.routes" :key="mode">
          <h4>
            {{ mode }} ·
            {{ version.route_elapsed_ms?.[mode] ?? route.elapsed_ms }}
            ms（该路服务耗时）
          </h4>
          <table>
            <thead>
              <tr>
                <th>文档 / 标签</th>
                <th>命中文本</th>
                <th>分数种类 / 原分数</th>
                <th>实际来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="hit in route.hits" :key="hit.doc_id">
                <td>{{ hit.doc_id }} / {{ hit.intent_code }}</td>
                <td>{{ hit.content }}</td>
                <td>{{ hit.score_kind }} / {{ hit.raw_score }}</td>
                <td><RawDetails title="来源证据" :value="hit.metadata" /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
    <RawDetails
      v-if="task"
      title="原任务、实际用量、构建清单及改善 / 退化指标"
      :value="task"
    />
  </section>
</template>
