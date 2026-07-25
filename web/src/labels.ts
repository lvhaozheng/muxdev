import type { ActivityEvent } from "./types";

export const statusLabels: Record<string, string> = {
  idle: "待命",
  clarifying: "需要澄清",
  working: "进行中",
  needs_user: "需要你确认",
  candidate_ready: "可评审",
  verifying: "验证中",
  recovering: "恢复中",
  awaiting_acceptance: "等待验收",
  delivered: "已交付",
  closed: "已关闭",
  queued: "排队中",
  running: "运行中",
  waiting_user: "等待输入",
  reported: "已回报",
  ready_to_merge: "待合并",
  merging: "合并中",
  completed: "已完成",
  blocked: "受阻",
  cancelled: "已取消",
  ready: "就绪",
  resumable: "可恢复",
  failed: "失败",
  passed: "已通过",
  skipped: "已跳过",
  unavailable: "不可用",
  accepted: "已接受",
  rolled_back: "已回退",
  answered: "已回答",
  superseded: "已取代",
  pending: "待评审",
};

export function labelStatus(value?: string | null): string {
  return statusLabels[value ?? ""] ?? value ?? "未知";
}

const attentionLabels: Record<string, string> = {
  needs_you: "需要你处理",
  active: "进行中",
  ready: "待评审",
  history: "历史",
};

export function labelAttention(value?: string | null): string {
  return attentionLabels[value ?? ""] ?? "未知";
}

const eventLabels: Record<string, string> = {
  "conversation.created": "会话已创建",
  "conversation.mode_selected": "协作方式已确定",
  "conversation.paused": "会话已暂停",
  "conversation.closed": "会话已关闭",
  "conversation.reopened": "会话已重新打开",
  "contract.created": "交付契约已创建",
  "contract.revised": "交付契约已更新",
  "workspace.prepared": "工作区已准备",
  "workspace.drift_detected": "检测到工作区漂移",
  "requirements.assessment_requested": "正在确认交付要求",
  "requirements.needs_input": "交付要求需要补充",
  "requirements.ready": "交付要求已冻结",
  "interaction.created": "需要你确认一个问题",
  "interaction.responded": "已收到你的回答",
  "interaction.answer_received": "已收到你的回答",
  "user.message": "你发送了新消息",
  "supervisor.message": "调度器发送了消息",
  "assignment.created": "已创建协作任务",
  "assignment.started": "Agent 开始工作",
  "assignment.reported": "Agent 已回报结果",
  "assignment.merged": "协作变更已合并",
  "assignment.merge_conflict": "协作变更发生冲突",
  "file.changed": "文件发生变化",
  "workspace.baseline_captured": "已冻结运行基线",
  "workspace.reconciled": "已完成变更对账",
  "orchestration.plan_proposed": "多 Agent 计划已提出",
  "orchestration.plan_approved": "多 Agent 计划已批准",
  "orchestration.plan_revised_in_scope": "多 Agent 计划已在范围内修订",
  "orchestration.completed": "多 Agent 编排已完成",
  "verification.approved": "验证已获批准",
  "delivery.verification_requested": "开始交付验证",
  "delivery.candidate_ready": "交付候选已就绪",
  "delivery.verified": "交付验证已通过",
  "delivery.blocked": "交付验证受阻",
  "delivery.invalidated": "旧交付候选已失效",
  "delivery.accepted": "你已接受本轮交付",
  "delivery.answered": "本轮已回答",
  "workspace.rolled_back": "本轮变更已安全回退",
  "review.changes_requested": "你请求了修改",
  "run.requested": "已请求执行 Run",
  "run.failed_to_start": "Run 启动失败",
  "run.orphaned": "检测到孤立 Run",
  "recovery.requested": "已请求恢复",
  "recovery.failed": "恢复失败",
  "provider.session_bound": "Agent Session 已绑定",
  "preview.registered": "本地预览已登记",
  "memory.candidate": "已生成记忆候选",
  "memory.approved": "记忆候选已批准",
};

const runEventLabels: Record<string, string> = {
  "run.created": "验证 Run 已创建",
  "run.status_changed": "Run 状态已更新",
  "run.review_settled": "本轮评审已结算",
  "run.policy_frozen": "运行策略已冻结",
  "run.policy_snapshot.ready": "运行策略快照已就绪",
  "run.initializing": "Run 正在初始化",
  "job.started": "后台作业已开始",
  "job.finished": "后台作业已结束",
  "interaction.requested": "运行需要你的输入",
  "interaction.responded": "运行已收到输入",
  "interaction.resume": "运行已继续",
  "recovery.requested": "运行恢复已请求",
  "recovery.session": "恢复 Session 已建立",
  "recovery.attempt": "正在尝试恢复",
  "recovery.feedback": "已生成恢复反馈",
  "recovery.exhausted": "安全恢复次数已用尽",
  "supervisor.fanout.started": "并行调度已开始",
  "supervisor.fanout.completed": "并行调度已完成",
  "runtime.capability_grant": "运行权限已授予",
  "provider.event": "Agent Runtime 活动",
  "workspace.delivery_deferred": "变更等待人工接受",
  "workspace.applied": "变更已安全写回",
  "evidence.record": "验证证据已记录",
};

export function eventTitle(event: ActivityEvent): string {
  if (event.type === "run.event") {
    const sourceType =
      typeof event.payload.source_type === "string"
        ? event.payload.source_type
        : "";
    return runEventLabels[sourceType] ?? "运行证据已记录";
  }
  return eventLabels[event.type] ?? "系统活动";
}

export function eventDetail(event: ActivityEvent): string {
  const payload = event.payload;
  if (event.type === "run.event") {
    const sourceType =
      typeof payload.source_type === "string" ? payload.source_type : "";
    const summary = typeof payload.summary === "string" ? payload.summary : "";
    if (!summary || summary === sourceType) return "";
    if (typeof payload.status === "string" && summary === payload.status) {
      return labelStatus(summary);
    }
    return summary;
  }
  for (const key of ["summary", "message", "prompt", "content", "reason", "status"]) {
    if (typeof payload[key] === "string" && payload[key]) {
      return key === "status"
        ? labelStatus(String(payload[key]))
        : String(payload[key]);
    }
  }
  if (typeof payload.path === "string") {
    const stats = [
      Number(payload.additions) ? `+${payload.additions}` : "",
      Number(payload.deletions) ? `−${payload.deletions}` : "",
    ]
      .filter(Boolean)
      .join(" ");
    return `${payload.path}${stats ? ` · ${stats}` : ""}`;
  }
  return "";
}

export function labelActor(value: string): string {
  return {
    developer: "你",
    supervisor: "调度器",
    runtime: "运行时",
    system: "系统",
  }[value] ?? value;
}

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
