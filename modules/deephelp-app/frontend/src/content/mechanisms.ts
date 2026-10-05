const root = "https://github.com/yuweilan7/deephelp/blob/main/";
export const mechanisms = {
  request: {
    lesson: "08-message-path.md",
    code: "modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py",
    entry: "Conversation.run",
    experiment: "course request",
    question: "为什么缺订单号时不能查工具？",
  },
  retrieval: {
    lesson: "10-retrieval.md",
    code: "modules/deephelp-app/src/deephelp_app/application/hybrid.py",
    entry: "HybridRetriever.compare / result_for",
    experiment: "course retrieval",
    question: "候选没被召回，重排还能把它找回来吗？",
  },
  memory: {
    lesson: "11-memory.md",
    code: "modules/deephelp-app/src/deephelp_app/application/memory.py",
    entry: "MemoryService.load / ProjectionWorker.once",
    experiment: "course memory",
    question: "Redis缺失会不会让PG原回执消失？",
  },
  protocol: {
    lesson: "09-agent-basics.md",
    code: "modules/deephelp-app/src/deephelp_app/adapters/mcp/business/tool_gateway.py",
    entry: "ToolGateway.execute",
    experiment:
      "pytest modules/deephelp-app/tests/integration/test_m06_stdio.py -q",
    question: "客户端非法参数为何根本不该发送？",
  },
  workflow: {
    lesson: "12-approval.md",
    code: "modules/deephelp-app/src/deephelp_app/application/approval.py",
    entry: "ApprovalService.graph / resume",
    experiment:
      "pytest modules/deephelp-app/tests/unit/approval/test_m15_approval.py -k unknown_queries_before_any_redispatch -q",
    question: "UNKNOWN为什么必须先对账？",
  },
  observation: {
    lesson: "13-observations.md",
    code: "modules/deephelp-app/src/deephelp_app/application/observation_explanation.py",
    entry: "observation_summary",
    experiment: "course observations",
    question: "两条重叠span的时长为何不能相加？",
  },
};
export type Mechanism = keyof typeof mechanisms;
export const lessonUrl = (key: Mechanism) =>
  root + "docs/learning/" + mechanisms[key].lesson;
export const codeUrl = (key: Mechanism) => root + mechanisms[key].code;
export function experimentCommand(key: Mechanism) {
  const command = mechanisms[key].experiment;
  return (
    "py -3.14 -m uv run --locked " +
    (command.startsWith("course ")
      ? "python -m deephelp_tools.learning."
      : "") +
    command
  );
}
