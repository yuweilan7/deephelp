export const navigation = [
  { path: "/", label: "从哪里开始" },
  { path: "/conversation", label: "对话与全过程" },
  { path: "/business", label: "业务资料" },
  { path: "/analysis", label: "识别与检索" },
  { path: "/memory", label: "多轮与记忆" },
  { path: "/tools", label: "工具与工作流" },
  { path: "/observation", label: "观测诊断" },
  { path: "/evaluation", label: "评测与改进" },
];
export function statusLabel(value: string): string {
  return (
    (
      {
        ANSWERED: "已回答",
        CLARIFY: "等待补充",
        PENDING_APPROVAL: "等待审批",
        HANDOFF: "转交处理",
        ERROR: "执行失败",
        REJECTED: "已拒绝",
        RUNNING: "运行中",
        CANCELLING: "正在取消",
        CANCELLED: "已取消",
        INTERRUPTED: "已中断",
        FAILED: "失败",
        SUCCEEDED: "成功",
        PENDING: "未审批",
        APPROVED: "已批准",
        completed: "完成",
        skipped: "跳过",
        disabled: "未启用",
        failed: "失败",
      } as Record<string, string>
    )[value] || value
  );
}
export function factLabel(value: string): string {
  return (
    (
      {
        order_id: "订单编号",
        paid: "实付金额",
        discount: "优惠金额",
        discount_status: "优惠状态",
        activity_ids: "活动编号",
        activity_labels: "活动名称",
        sop_conclusion: "核对结论",
        coupon_id: "优惠券编号",
        coupon_status: "优惠券状态",
      } as Record<string, string>
    )[value] || value
  );
}
