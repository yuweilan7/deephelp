import { createRouter, createWebHashHistory } from "vue-router";
import HomePage from "../features/home/HomePage.vue";
import ConversationPage from "../features/conversation/ConversationPage.vue";
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: HomePage },
    { path: "/conversation", component: ConversationPage },
  ],
});
