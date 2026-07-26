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
  busy: "执行中",
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
  "message.queued": "消息已排队",
  "message.dispatched": "消息已交给 Agent",
  "message.delivery_failed": "消息投递失败",
  "message.delivery_uncertain": "消息投递状态不确定",
  "agent.message": "Agent 消息",
  "assistant.message": "Agent 消息",
  "provider.message": "Agent 消息",
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
  "skill.loaded": "Skill 已验证加载",
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

const eventExplanations: Record<string, string> = {
  "conversation.created": "已建立任务对话和可追踪的活动账本。",
  "conversation.mode_selected": "已确定本轮由单 Agent 或多 Agent 协作执行。",
  "contract.created": "已把目标、范围和验收要求写入交付契约。",
  "contract.revised": "已根据新的人工输入更新交付契约。",
  "workspace.prepared": "已准备隔离工作区，Agent 的修改会在其中执行和对账。",
  "workspace.baseline_captured": "已记录执行前文件状态，后续变更可以确定性比较。",
  "workspace.reconciled": "已把运行结果与基线对比，形成可验证的文件变化。",
  "requirements.assessment_requested": "正在判断任务信息是否足以安全开始执行。",
  "requirements.ready": "需求、范围和完成条件已经冻结，可以开始执行。",
  "assignment.created": "调度器已把一部分工作分配给具体 Agent。",
  "assignment.started": "Agent Session 已接收任务并进入实际执行。",
  "assignment.reported": "Agent 已提交本次任务结果，等待验证或合并。",
  "assignment.merged": "该 Agent 的已验证变更已经安全合并。",
  "assignment.merge_conflict": "Agent 变更与当前基线冲突，已进入受控冲突处理。",
  "provider.session_bound": "外部 CLI Session 已与本次任务建立可追踪关联。",
  "run.requested": "已创建执行请求，运行时正准备启动 Agent。",
  "agent.message": "Agent 已返回新的进度、判断或执行结果。",
  "assistant.message": "Agent 已返回新的进度、判断或执行结果。",
  "provider.message": "外部 Agent CLI 已返回新的消息内容。",
  "message.queued": "CLI 尚未稳定就绪；消息已可靠保存，解除阻塞后会自动续送。",
  "message.dispatched": "消息已写入明确的 Session generation，并恢复对应任务。",
  "message.delivery_failed": "消息没有进入 Agent CLI，需要按提示修复 Session 后重试。",
  "message.delivery_uncertain": "CLI 在投递边界退出，系统不会冒险自动重复执行。",
  "run.failed_to_start": "Agent CLI 没有成功启动，需要检查环境或重试。",
  "skill.loaded": "Agent 已通过受审计通道加载冻结版本的 Skill。",
  "file.changed": "运行时观察到工作区文件发生变化，最终仍需完成对账。",
  "delivery.verification_requested": "已开始检查交付项、测试结果和证据完整性。",
  "delivery.candidate_ready": "变更与证据已形成可供人工评审的交付候选。",
  "delivery.verified": "当前交付候选已通过配置的验证门禁。",
  "delivery.blocked": "交付门禁尚未满足，当前结果不能标记为可信完成。",
  "orchestration.plan_proposed": "编排 Agent 已生成包含任务依赖的多 Agent 计划。",
  "orchestration.plan_approved": "多 Agent 计划已冻结并允许调度执行。",
  "orchestration.completed": "编排计划中的任务已经完成并结算。",
  "memory.candidate": "系统根据本轮交付生成了可复用记忆候选。",
  "memory.approved": "人工已批准记忆候选，后续任务可以安全复用。",
};

const runEventExplanations: Record<string, string> = {
  "run.created": "已建立可审计的 Run 记录。",
  "run.status_changed": "Run 的生命周期状态发生变化。",
  "run.policy_frozen": "本次 Run 的权限和交付策略已经冻结。",
  "run.policy_snapshot.ready": "执行策略快照已生成，可用于回放和验证。",
  "run.initializing": "运行时正在准备 Agent、工作区和所需上下文。",
  "job.started": "后台执行单元已经开始工作。",
  "job.finished": "后台执行单元已经结束并返回状态。",
  "provider.event": "外部 Agent CLI 返回了新的运行活动。",
  "runtime.capability_grant": "运行时记录了本次任务允许使用的能力边界。",
  "evidence.record": "一条测试、检查或交付证据已写入证据账本。",
  "workspace.delivery_deferred": "变更已准备好，但在人工接受前不会写回。",
  "workspace.applied": "已验证变更已经写回目标工作区。",
};

export function eventSemanticType(event: ActivityEvent): string {
  if (event.type !== "run.event") return event.type;
  const sourceType =
    typeof event.payload.source_type === "string"
      ? event.payload.source_type
      : "unknown";
  return `run.event:${sourceType}`;
}

export function eventExplanation(event: ActivityEvent): string {
  if (event.type === "run.event") {
    const sourceType =
      typeof event.payload.source_type === "string"
        ? event.payload.source_type
        : "";
    return (
      runEventExplanations[sourceType] ??
      "运行时已记录同类活动；展开后可以查看每条原始状态和证据等级。"
    );
  }
  return (
    eventExplanations[event.type] ??
    "系统已记录同类活动；展开后可以查看每条原始状态、参与方和证据等级。"
  );
}

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
  if (event.type === "message.queued" && typeof payload.reason === "string") {
    return {
      session_starting: "Session 正在启动",
      session_transitioning: "Session 正在中断或重建",
      session_waiting_input: "CLI 正在等待额度、登录或人工确认",
      session_waiting_for_ready_prompt: "已处理 Terminal，正在等待 CLI 恢复输入状态",
    }[payload.reason] ?? payload.reason;
  }
  if (
    event.type === "skill.loaded" &&
    typeof payload.qualified_name === "string"
  ) {
    const revision =
      typeof payload.revision === "string" ? ` · ${payload.revision}` : "";
    return `${payload.qualified_name}${revision}`;
  }
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
