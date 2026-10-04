"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  csrf: null,
  epoch: 0,
  session: null,
  hint: null,
  pending: null,
  last: null,
  record: null,
  kind: "orders",
  sessionsOffset: null,
  messagesOffset: null,
  businessOffset: null,
  demo: null,
};
const node = (tag, text, className) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = String(text);
  if (className) n.className = className;
  return n;
};
function notify(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
async function api(path, method = "GET", body) {
  const epoch = state.epoch;
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(state.csrf ? { "X-CSRF-Token": state.csrf } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (epoch !== state.epoch) throw new Error("登录状态已改变，请重新操作。");
  if (!response.ok) {
    if (response.status === 401 && state.csrf) clearSession();
    const error = new Error(
      data.reply || data.error?.message || `请求失败 (${response.status})`,
    );
    error.status = response.status;
    throw error;
  }
  return data;
}
function action(label, fn) {
  const b = node("button", label);
  b.type = "button";
  b.addEventListener("click", () => run(fn));
  return b;
}
async function run(fn) {
  try {
    await fn();
  } catch (e) {
    notify(e.message, true);
  }
}
function clearSession() {
  state.epoch++;
  Object.assign(state, {
    csrf: null,
    session: null,
    hint: null,
    pending: null,
    last: null,
    record: null,
    demo: null,
  });
  for (const id of [
    "sessions",
    "messages",
    "inspection",
    "current-result",
    "business-records",
    "record-fields",
    "record-result",
    "execution",
    "protocol-result",
    "workflow-result",
    "demo-result",
    "demo-summary",
    "workflow-graph",
  ])
    $(id).replaceChildren();
  $("text").value = "";
  $("activity-ids").value = "";
  $("token").value = "";
  $("workspace").hidden = true;
  $("logout").hidden = true;
  $("login-panel").hidden = false;
  $("identity").textContent = "尚未登录";
  for (const id of [
    "retry",
    "replay",
    "relations",
    "more-sessions",
    "more-messages",
    "more-business",
    "clear-hint",
  ])
    $(id).hidden = true;
  $("send").disabled = false;
  $("text").disabled = false;
  for (const n of document.querySelectorAll("[data-learning]")) n.hidden = true;
  for (const id of ["demo-operation", "demo-session", "demo-run"])
    $(id).value = "";
  $("demo-confirm").checked = false;
  demoButtons();
}
function showIdentity(data) {
  state.csrf = data.csrf;
  $("identity").textContent =
    `${data.identity.tenant_id} / ${data.identity.user_id}`;
  $("workspace").hidden = false;
  $("login-panel").hidden = true;
  $("logout").hidden = false;
  const e = data.execution;
  $("execution").textContent =
    `模式：${e.mode} · 事实来源：${e.business_source === "mysql" ? "MySQL个人持久资料" : "显式demo"} · 单条预计${e.expected_calls_per_message} · 剩余调用${e.remaining_calls} · 共享token上限${e.max_tokens} / 已计${e.charged_tokens} · 超时${e.request_timeout_seconds}秒。点击执行才调用模型。`;
  $("business-mode").textContent = e.business_maintenance
    ? "已连接Business服务。保存不调用模型。"
    : "当前后端未启用MySQL业务维护；请按启动说明选择Business服务。";
  $("save-record").disabled = !e.business_maintenance;
  run(learningStatus);
}
function newSession() {
  state.session = crypto.randomUUID();
  state.hint = null;
  state.pending = null;
  state.last = null;
  $("session-id").textContent = state.session;
  $("messages").replaceChildren(
    node("p", "这里还没有消息。提交后写入持久账本。", "muted"),
  );
  $("inspection").replaceChildren();
  $("current-result").replaceChildren();
  $("more-messages").hidden = true;
  $("retry").hidden = true;
  $("replay").hidden = true;
  $("text").disabled = false;
  setHint(null);
}
function setHint(q) {
  state.hint = q;
  $("continuation").textContent = q
    ? `补充问题 ${q.question_id} · 当前版本 ${q.version}`
    : "正在提出新问题";
  $("clear-hint").hidden = !q;
}
async function sessions(offset = 0) {
  const p = await api(`/history/sessions?offset=${offset}`);
  if (offset === 0) $("sessions").replaceChildren();
  for (const item of p.items) {
    const b = action(item.session_id, async () => {
      if (state.pending)
        throw new Error("原消息结果待确认，请先重试或新建会话。");
      state.session = item.session_id;
      setHint(null);
      $("session-id").textContent = state.session;
      await messages();
    });
    b.className = "session";
    b.append(
      node(
        "small",
        `${item.message_count}条 · ${new Date(item.updated_at).toLocaleString()}`,
      ),
    );
    $("sessions").append(b);
  }
  if (!p.items.length && offset === 0)
    $("sessions").append(node("p", "尚无持久会话", "muted"));
  state.sessionsOffset = p.next_offset;
  $("more-sessions").hidden = p.next_offset === null;
}
async function messages(offset = 0) {
  const sid = state.session;
  const p = await api(
    `/history/sessions/${encodeURIComponent(sid)}/messages?offset=${offset}`,
  );
  if (sid !== state.session) return;
  if (offset === 0) $("messages").replaceChildren();
  for (const item of p.items) {
    const turn = node("article", undefined, "turn");
    turn.append(node("p", item.input.raw_text, "user"));
    const r = item.response;
    turn.append(
      node("p", r?.reply || `执行状态：${item.status}。未完成结果不自动重领。`),
    );
    turn.append(
      node(
        "div",
        `原执行 ${r?.outcome || item.status} · ${new Date(item.input.occurred_at).toLocaleString()} · run ${item.run_id}`,
        "meta",
      ),
    );
    if (r) {
      turn.append(action("事实 / 时间线", () => inspect(r)));
      turn.append(
        action("重投此消息", async () => {
          if (state.pending) throw new Error("请先处理待确认消息。");
          state.last = item.input;
          await send(item.input);
        }),
      );
      state.last = item.input;
      $("replay").hidden = false;
    }
    if (["ACTIVE", "WAITING_SLOT"].includes(item.question.status))
      turn.append(
        action("补充此问题", () => {
          setHint(item.question);
          $("text").focus();
        }),
      );
    $("messages").append(turn);
  }
  state.messagesOffset = p.next_offset;
  $("more-messages").hidden = p.next_offset === null;
}
function detail(label, value, parent = $("inspection")) {
  const d = node("details");
  d.append(node("summary", label), node("pre", JSON.stringify(value, null, 2)));
  parent.append(d);
}
async function inspect(r) {
  const sid = state.session;
  const box = $("inspection");
  box.replaceChildren(
    node("p", `run ${r.run_id}`, "code"),
    node("p", `本次返回关联 trace ${r.trace_id}`, "code"),
  );
  box.append(node("span", r.outcome, "badge"));
  for (const f of r.facts || [])
    box.append(
      node(
        "div",
        `${f.name}: ${typeof f.value === "object" ? JSON.stringify(f.value) : f.value}`,
        "fact",
      ),
    );
  detail("原始事实与不可变证据", {
    facts: r.facts,
    evidence_refs: r.evidence_refs,
    tool_call_ids: r.tool_call_ids,
  });
  for (const ev of r.evidence_refs || []) {
    const id = ev.evidence_id;
    if (id)
      box.append(
        action(`查询证据 ${id}`, async () => {
          const value = await api(
            `/business/evidence/${encodeURIComponent(id)}`,
          );
          if (sid === state.session) detail("Business原版本证据", value);
        }),
      );
  }
  detail("原执行计数 / 版本", {
    call_counts: r.call_counts,
    budget_used: r.budget_used,
    versions: r.versions,
    replayed: r.replayed,
  });
  box.append(node("h3", "13段主链 · 原执行"));
  for (const s of r.stages || []) {
    const row = node("div", `${s.stage} · ${s.status}`, "stage");
    row.append(
      node(
        "small",
        `${s.elapsed_ms ?? "未知"} ms · 模型${s.model_calls ?? 0} / 工具${s.tool_calls ?? 0}`,
      ),
    );
    box.append(row);
  }
  if (r.run_id) {
    try {
      const flow = await api(
        `/debug/runs/${encodeURIComponent(r.run_id)}/flow?session_id=${encodeURIComponent(sid)}`,
      );
      if (sid !== state.session) return;
      if (
        flow.run_id !== r.run_id ||
        (!r.replayed && flow.trace_id !== r.trace_id)
      )
        throw new Error("追踪与当前结果不一致。");
      box.append(node("p", `原执行 trace ${flow.trace_id}`, "code"));
      detail("实际SOP节点 / MCP / 检索", flow.data);
      if (flow.incomplete?.length || flow.dropped_events)
        box.append(
          node(
            "p",
            `诊断不完整：${JSON.stringify(flow.incomplete)}；丢弃${flow.dropped_events}项`,
            "muted",
          ),
        );
      box.append(
        action("读取同次OTel子span", async () => {
          const obs = await api(
            `/observability/runs/${encodeURIComponent(r.run_id)}?session_id=${encodeURIComponent(sid)}`,
          );
          if (sid === state.session)
            detail("专业平台实际观测（不可用时明确标记）", obs);
        }),
      );
    } catch (e) {
      box.append(
        node("p", `诊断不可用：${e.message}。持久业务结果仍在。`, "muted"),
      );
    }
  }
}
async function send(payload) {
  if (state.pending && state.pending.message_id !== payload.message_id)
    throw new Error("请先重试结果待确认的原消息。");
  state.pending = payload;
  const sid = state.session;
  $("send").disabled = true;
  $("retry").hidden = true;
  $("text").disabled = true;
  notify("正在执行；网络重试将沿用原消息ID。");
  try {
    const r = await api("/converse", "POST", payload);
    state.pending = null;
    state.last = payload;
    $("replay").hidden = false;
    $("current-result").replaceChildren(
      node(
        "p",
        `本次返回：${r.outcome} · ${r.replayed ? "原receipt重放" : "新执行"} · 模型${r.call_counts?.model_calls ?? 0} / 检索${r.call_counts?.retrieval_calls ?? 0} / 工具${r.call_counts?.tool_calls ?? 0} · ${r.reply}`,
      ),
    );
    if (sid === state.session) {
      $("text").value = "";
      setHint(null);
      await messages();
      await inspect(r);
    }
    await sessions();
    showIdentity(await api("/auth/session"));
    notify(
      r.replayed
        ? "重投返回原事实，本次调用计数见当前返回。"
        : "结果已写入持久账本。",
    );
  } catch (e) {
    // Explicit input/ownership/version rejection did not authorize a new execution.
    // Uncertain network/server failures preserve the complete original payload.
    if ([400, 403, 404, 409, 422].includes(e.status)) {
      state.pending = null;
      $("retry").hidden = true;
      throw e;
    }
    if (state.csrf && state.pending) {
      $("retry").hidden = false;
      notify(`${e.message} 原消息已保留，重试会使用相同ID、正文和时间。`, true);
    } else throw e;
  } finally {
    if (state.csrf) {
      $("send").disabled = Boolean(state.pending);
      $("text").disabled = Boolean(state.pending);
    }
  }
}
const fields = {
  orders: [
    ["order_id", "订单ID"],
    ["paid", "实付金额（CNY）"],
    ["discount", "优惠金额（CNY）"],
    ["discount_status", "优惠状态", ["applied", "not_eligible", "missing"]],
    ["sku_ids", "商品ID（逗号分隔）"],
    ["status", "记录状态", ["active", "inactive"]],
  ],
  coupons: [
    ["coupon_id", "券ID"],
    ["order_id", "所属订单ID"],
    ["minimum_spend", "门槛金额（CNY）"],
    [
      "status",
      "券状态",
      [
        "usable",
        "expired",
        "threshold_not_met",
        "already_used",
        "not_started",
        "scope_mismatch",
        "frozen",
        "revoked",
      ],
    ],
    ["valid_from", "开始时间（含时区）"],
    ["valid_until", "结束时间（含时区）"],
  ],
  activities: [
    ["activity_id", "活动ID"],
    ["label", "活动名称"],
    ["rules", "规则说明"],
    ["status", "活动状态", ["active", "inactive"]],
    ["valid_from", "开始时间（含时区）"],
    ["valid_until", "结束时间（含时区）"],
  ],
};
function editor(record = null) {
  state.record = record;
  const data = record?.data || {};
  $("editor-title").textContent =
    `${record ? "编辑" : "新建"}${{ orders: "订单", coupons: "券", activities: "活动" }[state.kind]}${record ? ` · 版本${record.version}` : ""}`;
  const box = $("record-fields");
  box.replaceChildren();
  for (const [key, label, options] of fields[state.kind]) {
    const l = node("label", label);
    l.htmlFor = "field-" + key;
    const input = node(options ? "select" : "input");
    input.id = "field-" + key;
    input.name = key;
    input.required = true;
    if (options)
      for (const v of options) {
        const o = node("option", v);
        o.value = v;
        input.append(o);
      }
    let value = data[key];
    if (["paid", "discount", "minimum_spend"].includes(key))
      value = value?.amount ?? "0.00";
    if (key === "sku_ids") value = value?.join(",") ?? "SKU-1";
    if (key === "valid_from")
      value = value ?? new Date(Date.now() - 86400000).toISOString();
    if (key === "valid_until")
      value = value ?? new Date(Date.now() + 365 * 86400000).toISOString();
    if (value !== undefined) input.value = value;
    input.readOnly = Boolean(
      record && key.endsWith("_id") && key === fields[state.kind][0][0],
    );
    box.append(l, input);
  }
  $("relations").hidden = !(record && state.kind === "orders");
  $("record-result").replaceChildren();
}
function recordBody() {
  const data = {};
  for (const [key] of fields[state.kind]) {
    let v = $("field-" + key).value.trim();
    if (["paid", "discount", "minimum_spend"].includes(key))
      v = { amount: v, currency: "CNY" };
    if (key === "sku_ids")
      v = v
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);
    data[key] = v;
  }
  if (state.kind === "activities") data.scope = "owner";
  return data;
}
async function business(offset = 0) {
  const kind = state.kind;
  const p = await api(`/business/${kind}?offset=${offset}`);
  if (kind !== state.kind) return;
  if (offset === 0) $("business-records").replaceChildren();
  for (const record of p.items) {
    const id = record.data[fields[kind][0][0]];
    const b = action(`${id} · 版本${record.version}`, () => loadRecord(id));
    b.className = "record";
    $("business-records").append(b);
  }
  if (!p.items.length && offset === 0)
    $("business-records").append(node("p", "当前身份暂无资料。", "muted"));
  state.businessOffset = p.next_offset;
  $("more-business").hidden = p.next_offset === null;
}
async function loadRecord(id) {
  const kind = state.kind;
  const record = await api(`/business/${kind}/${encodeURIComponent(id)}`);
  if (kind !== state.kind) return;
  editor(record);
  if (kind === "orders") {
    const rel = await api(
      `/business/orders/${encodeURIComponent(id)}/activities`,
    );
    $("activity-ids").value = rel.activity_ids.join(",");
  }
  $("record-result").textContent = JSON.stringify(record, null, 2);
}
$("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {
    const token = $("token").value;
    $("token").value = "";
    showIdentity(await api("/auth/login", "POST", { token }));
    newSession();
    editor();
    await sessions();
    notify("已登录。浏览与资料维护不调用模型。");
  });
});
$("logout").addEventListener("click", () =>
  run(async () => {
    await api("/auth/logout", "POST");
    clearSession();
    notify("已退出，当前页面中的业务内容已清除。");
  }),
);
$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {
    if (state.pending) throw new Error("请先重试原消息。");
    const hint = state.hint;
    await send({
      channel: "browser",
      session_id: state.session,
      message_id: crypto.randomUUID(),
      raw_text: $("text").value,
      occurred_at: new Date().toISOString(),
      question_hint: hint?.question_id ?? null,
      expected_question_version: hint?.version ?? null,
    });
  });
});
$("retry").addEventListener("click", () => run(() => send(state.pending)));
$("replay").addEventListener("click", () => run(() => send(state.last)));
$("new-session").addEventListener("click", () => {
  if (state.pending) {
    notify("原消息仍待确认，请先重试。", true);
    return;
  }
  newSession();
});
$("clear-hint").addEventListener("click", () => setHint(null));
$("reload-sessions").addEventListener("click", () =>
  run(async () => {
    await sessions();
    if (state.last) await messages();
    notify("已从MySQL重载，未请求模型。");
  }),
);
$("more-sessions").addEventListener("click", () =>
  run(() => sessions(state.sessionsOffset)),
);
$("more-messages").addEventListener("click", () =>
  run(() => messages(state.messagesOffset)),
);
for (const b of document.querySelectorAll("[data-tab]"))
  b.addEventListener("click", () => {
    for (const tab of document.querySelectorAll(".tab"))
      tab.hidden = tab.id !== b.dataset.tab;
    for (const btn of document.querySelectorAll("[data-tab]"))
      btn.classList.toggle("selected", btn === b);
    if (b.dataset.tab === "business") run(() => business());
    if (b.dataset.tab === "workflow") run(loadWorkflow);
  });
$("kind").addEventListener("change", () => {
  state.kind = $("kind").value;
  $("business-records").replaceChildren();
  editor();
  run(() => business());
});
$("new-record").addEventListener("click", () => editor());
$("reload-business").addEventListener("click", () => run(() => business()));
$("more-business").addEventListener("click", () =>
  run(() => business(state.businessOffset)),
);
$("record-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {
    const data = recordBody();
    const old = state.record;
    const kind = state.kind;
    const path = `/business/${kind}${old ? "/" + encodeURIComponent(data[fields[kind][0][0]]) : ""}`;
    const record = await api(
      path,
      old ? "PUT" : "POST",
      old ? { expected_version: old.version, data } : data,
    );
    if (kind !== state.kind) return;
    editor(record);
    $("record-result").textContent = JSON.stringify(record, null, 2);
    await business();
    notify("资料已保存。请用新消息验证更新后的事实。");
  });
});
$("save-relations").addEventListener("click", () =>
  run(async () => {
    const r = state.record;
    const id = r.data.order_id;
    const activity_ids = $("activity-ids")
      .value.split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    await api(`/business/orders/${encodeURIComponent(id)}/activities`, "PUT", {
      expected_version: r.version,
      activity_ids,
    });
    await loadRecord(id);
    await business();
    notify("活动关系已保存，订单版本已更新。");
  }),
);
async function learningStatus() {
  try {
    const s = await api("/learning/status");
    for (const n of document.querySelectorAll("[data-learning]"))
      n.hidden = !s.installed;
    $("demo-create").disabled = !s.demo_enabled;
    $("demo-prerequisite").textContent = s.demo_enabled
      ? "独立演示身份与权益执行口已启用。"
      : `需显式配置合成权益服务，并用 ${s.demo_identity_required} 的受信令牌登录。`;
  } catch (e) {
    if (e.status !== 404) throw e;
  }
}
async function loadWorkflow() {
  const d = await api("/learning/workflow");
  $("workflow-result").textContent = JSON.stringify(d, null, 2);
  $("workflow-graph").replaceChildren();
  if (d.approval_graph)
    for (const edge of d.approval_graph.edges)
      $("workflow-graph").append(
        node("p", `${edge.source} → ${edge.target}`, "code"),
      );
  else
    $("workflow-graph").append(
      node("p", "合成执行口尚未配置；当前只读 SOP 已显示。"),
    );
}
function demoButtons() {
  const op = state.demo;
  $("demo-approve").disabled = !op || op.approval_status !== "PENDING";
  $("demo-reject").disabled = !op || op.approval_status !== "PENDING";
  $("demo-revoke").disabled = !op || op.approval_status !== "APPROVED";
  if (op && ["SUCCEEDED", "FAILED", "CANCELLED"].includes(op.status))
    $("demo-revoke").disabled = true;
  $("demo-resume").disabled = !op;
}
function bindDemo(op) {
  state.demo = op;
  $("demo-operation").value = op.plan.operation_id;
  $("demo-session").value = op.session_id;
  $("demo-run").value = op.run_id;
  $("demo-confirm").checked = false;
  demoButtons();
}
async function readDemo() {
  const query = new URLSearchParams({
    session_id: $("demo-session").value,
    run_id: $("demo-run").value,
  });
  const d = await api(
    `/learning/demo/${encodeURIComponent($("demo-operation").value)}?${query}`,
  );
  bindDemo(d.operation);
  $("demo-result").textContent = JSON.stringify(d, null, 2);
  $("demo-summary").replaceChildren(
    node(
      "h3",
      `状态 ${d.operation.status} · 审批 ${d.operation.approval_status}`,
    ),
    node(
      "p",
      `持久效果 ${d.effects} · 执行 ${d.operation.dispatch_attempts} · 对账查询 ${d.operation.query_attempts} · checkpoint ${d.checkpoints.length} · 下一节点 ${d.next.join(", ") || "已结束"}`,
    ),
  );
  notify("已按原绑定读取持久状态。浏览不调用模型。");
}
$("protocol-discover").addEventListener("click", () =>
  run(async () => {
    $("protocol-result").textContent = JSON.stringify(
      await api("/learning/protocol"),
      null,
      2,
    );
  }),
);
$("protocol-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {
    $("protocol-result").textContent = JSON.stringify(
      await api("/learning/protocol", "POST", {
        action: $("protocol-action").value,
        order_id: $("protocol-order").value,
        coupon_id: $("protocol-coupon").value,
      }),
      null,
      2,
    );
  });
});
$("workflow-load").addEventListener("click", () => run(loadWorkflow));
$("demo-create").addEventListener("click", () =>
  run(async () => {
    $("demo-create").disabled = true;
    try {
      bindDemo(await api("/learning/demo", "POST"));
      await readDemo();
    } finally {
      await learningStatus();
    }
  }),
);
$("demo-load-form").addEventListener("submit", (e) => {
  e.preventDefault();
  run(readDemo);
});
for (const decision of ["approve", "reject", "revoke"])
  $("demo-" + decision).addEventListener("click", () =>
    run(async () => {
      if (!$("demo-confirm").checked)
        throw new Error("请先核对显示的绑定并独立确认。");
      const op = state.demo;
      await api(
        `/learning/demo/${encodeURIComponent(op.plan.operation_id)}/approval`,
        "POST",
        {
          session_id: op.session_id,
          run_id: op.run_id,
          expected_question_version: op.question_version,
          parameters_hash: op.plan.parameters_hash,
          sop_version: op.plan.sop_version,
          snapshot_hash: op.plan.snapshot_hash,
          decision,
        },
      );
      await readDemo();
    }),
  );
$("demo-resume").addEventListener("click", () =>
  run(async () => {
    const op = state.demo;
    await api(
      `/learning/demo/${encodeURIComponent(op.plan.operation_id)}/resume`,
      "POST",
      { session_id: op.session_id, run_id: op.run_id },
    );
    await readDemo();
  }),
);
run(async () => {
  try {
    const data = await api("/auth/session");
    showIdentity(data);
    newSession();
    editor();
    await sessions();
  } catch (e) {
    notify(
      e.message.includes("请登录") ? "使用已配置令牌登录后开始。" : e.message,
      e.message.includes("未配置"),
    );
  }
});
