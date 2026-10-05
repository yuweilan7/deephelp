import { createRouter, createWebHashHistory } from "vue-router";
import HomePage from "../features/home/HomePage.vue";
import ConversationPage from "../features/conversation/ConversationPage.vue";
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: HomePage },
    { path: "/conversation", component: ConversationPage },
    {
      path: "/business",
      component: () => import("../features/business/BusinessPage.vue"),
    },
    {
      path: "/analysis",
      component: () => import("../features/analysis/AnalysisPage.vue"),
    },
    {
      path: "/memory",
      component: () => import("../features/memory/MemoryPage.vue"),
    },
    {
      path: "/tools",
      component: () => import("../features/tools/ToolsPage.vue"),
    },
    {
      path: "/observation",
      component: () => import("../features/observation/ObservationPage.vue"),
    },
    {
      path: "/evaluation",
      component: () => import("../features/evaluation/EvaluationPage.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
