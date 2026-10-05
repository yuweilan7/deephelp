<script setup lang="ts">
import { onMounted } from "vue";
import { workspace, connect } from "./composables/workspace";
import { navigation } from "./content/navigation";
onMounted(connect);
</script>
<template>
  <div class="shell">
    <aside class="sidebar">
      <router-link to="/" class="brand"
        >DeepHelp <span>个人学习工作台</span></router-link
      >
      <nav aria-label="学习任务">
        <router-link
          v-for="item in navigation"
          :key="item.path"
          :to="item.path"
          >{{ item.label }}</router-link
        >
      </nav>
      <p>浏览新增模型调用 0 次</p>
    </aside>
    <main>
      <header class="workspace-bar">
        <span>{{
          workspace.connection
            ? `${workspace.connection.identity.tenant_id} / ${workspace.connection.identity.user_id}`
            : "正在连接个人工作区…"
        }}</span
        ><el-button @click="connect">重新读取连接</el-button>
      </header>
      <el-alert
        v-if="workspace.error"
        :title="workspace.error"
        type="error"
        :closable="false"
      /><router-view />
    </main>
  </div>
</template>
