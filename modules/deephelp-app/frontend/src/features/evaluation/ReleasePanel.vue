<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../../api/client";
import type { Catalog } from "../../api/evaluation";
import { useRead, useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const props = defineProps<{ catalog: Catalog }>(),
  emit = defineEmits<{ refresh: [] }>();
const id = ref(""),
  confirmed = ref(false),
  release = useRead<{ version: string; eligible: boolean; error?: string }>(),
  action = useAction(),
  result = ref<unknown>(null);
watch(
  () => props.catalog.releases,
  (rows) => {
    if (!rows.some((r) => r.hash === id.value)) id.value = rows[0]?.hash || "";
  },
  { immediate: true },
);
watch(id, () => {
  confirmed.value = false;
  release.clear();
});
watch(
  () => props.catalog.pointer.revision,
  () => {
    confirmed.value = false;
    release.clear();
  },
);
function publish(mode: "publish" | "rollback") {
  if (
    !confirmed.value ||
    (mode === "publish"
      ? !release.data.value?.eligible
      : !props.catalog.pointer.previous)
  )
    return;
  void action.run(async () => {
    result.value = await api.request("/learning/evaluation/release", "POST", {
      action: mode,
      target: mode === "publish" ? id.value : props.catalog.pointer.previous,
      revision: props.catalog.pointer.revision,
      confirmed: true,
    });
    confirmed.value = false;
    emit("refresh");
  });
}
</script>
<template>
  <section class="card">
    <h2>完整版本与发布门禁</h2>
    <p class="identifier">
      PG {{ catalog.pointer.channel }} · revision {{ catalog.pointer.revision
      }}<br />active {{ catalog.pointer.active || "无" }}<br />previous
      {{ catalog.pointer.previous || "无" }}
    </p>
    <label for="release-id">已登记完整版本</label
    ><select id="release-id" v-model="id">
      <option v-for="r in catalog.releases" :key="r.hash" :value="r.hash">
        {{ r.version }} · {{ r.state }}
      </option></select
    ><el-button
      :disabled="!id"
      @click="
        release.load('/learning/evaluation/releases/' + encodeURIComponent(id));
        confirmed = false;
      "
      >核对当前发布资格</el-button
    ><StateNotice
      :error="release.error.value || action.error.value"
      :loading="release.loading.value"
    />
    <p v-if="release.data.value">
      {{ release.data.value.version }} · 当前门禁
      {{ release.data.value.eligible ? "通过" : "未通过" }} ·
      {{ release.data.value.error || "发布时仍复核 CAS / 字节 / 内容证据" }}
    </p>
    <RawDetails
      v-if="release.data.value"
      :value="release.data.value"
    /><el-checkbox v-model="confirmed"
      >已核对目标 hash 与当前 active revision，明确切换</el-checkbox
    >
    <div class="actions">
      <el-button
        :disabled="
          !confirmed || !release.data.value?.eligible || action.busy.value
        "
        @click="publish('publish')"
        >显式发布所选版本</el-button
      ><el-button
        :disabled="!confirmed || !catalog.pointer.previous || action.busy.value"
        @click="publish('rollback')"
        >显式回退 previous</el-button
      >
    </div>
    <RawDetails v-if="result" title="实际切换结果" :value="result" />
    <p>
      候选、平台分数、任务成功均不自动发布。旧清单属于原运行字节，当前不兼容时按原门禁拒绝。
    </p>
  </section>
</template>
