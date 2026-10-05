<script setup lang="ts">
import { ref } from "vue";
import { api } from "../../api/client";
import type { ProtocolDiscovery, ProtocolResult } from "../../api/learning";
import { factLabel } from "../../content/navigation";
import { useRead, useAction } from "../../composables/requests";
import RawDetails from "../../components/RawDetails.vue";
import StateNotice from "../../components/StateNotice.vue";
const discovery = useRead<ProtocolDiscovery>(),
  action = useAction(),
  result = ref<ProtocolResult | null>(null);
const mode = ref("benefits"),
  order = ref("90234105"),
  coupon = ref("90222004");
function execute() {
  void action.run(async () => {
    result.value = await api.request<ProtocolResult>(
      "/learning/protocol",
      "POST",
      {
        action: mode.value,
        order_id: order.value,
        coupon_id: coupon.value,
      },
    );
  });
}
</script>
<template>
  <section class="card">
    <h2>MCP 协议与双端校验</h2>
    <p>
      固定动作观察正式 initialize → tools/list → tools/call →
      structuredContent。协议成功与业务内容通过分别看原结果；此处不调用模型。
    </p>
    <el-button @click="discovery.load('/learning/protocol')"
      >读取实际握手与 schema</el-button
    ><StateNotice
      :error="discovery.error.value"
      :loading="discovery.loading.value"
    />
    <p v-if="discovery.data.value">
      实际工具 {{ discovery.data.value.tools.map((t) => t.name).join(" / ") }} ·
      来源 {{ discovery.data.value.source }} · 模型
      {{ discovery.data.value.model_calls }}
    </p>
    <RawDetails
      v-if="discovery.data.value"
      title="本次实际发现"
      :value="discovery.data.value"
    />
    <div v-if="result" class="route-result">
      <p>
        固定动作 {{ result.action }} ·
        {{
          result.rejected ? "已拒绝" : result.result?.status || "状态见原记录"
        }}
        · 实际发送 {{ result.dispatched ? "是" : "否" }} · 模型
        {{ result.model_calls }}
      </p>
      <p>校验边界 {{ result.boundary }} · trace {{ result.trace_id }}</p>
      <p v-for="(f, i) in result.result?.facts || []" :key="i">
        {{ factLabel(f.name) }}：{{
          typeof f.value === "object" ? JSON.stringify(f.value) : f.value
        }}
      </p>
      <p v-if="result.error">实际错误 {{ JSON.stringify(result.error) }}</p>
    </div>
    <form @submit.prevent="execute">
      <label for="protocol-action">固定实验动作</label
      ><select id="protocol-action" v-model="mode">
        <option value="benefits">查询订单优惠</option>
        <option value="coupon">查询优惠券</option>
        <option value="client_invalid">客户端非法参数（不发送）</option>
        <option value="server_invalid">
          服务端非法参数（拒绝查询）
        </option></select
      ><label for="protocol-order">个人订单编号</label
      ><input id="protocol-order" v-model="order" required /><label
        for="protocol-coupon"
        >个人优惠券编号</label
      ><input id="protocol-coupon" v-model="coupon" required /><el-button
        native-type="submit"
        :loading="action.busy.value"
        >明确执行协议实验 · 0 模型</el-button
      >
    </form>
    <StateNotice :error="action.error.value" />
    <p v-if="!result">
      尚未执行。客户端反例不发送；服务端反例必须实际发送并拒绝。网络错误不自动重试。
    </p>
    <RawDetails v-else title="本次双端校验与实际内容" :value="result" />
  </section>
</template>
