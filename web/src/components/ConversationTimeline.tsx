import {
  ArrowUp,
  CaretDown,
  CheckCircle,
  CircleNotch,
  GitBranch,
  PuzzlePiece,
  Robot,
  ShieldCheck,
  TerminalWindow,
  UserCircle,
  Warning,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  eventDetail,
  eventExplanation,
  eventSemanticType,
  eventTitle,
  formatTime,
  labelActor,
  labelStatus,
} from "../labels";
import type {
  ActivityEvent,
  Assignment,
  ConversationSnapshot,
  ProjectSkills,
} from "../types";

interface Props {
  snapshot: ConversationSnapshot;
  sending: boolean;
  skills?: ProjectSkills;
  onActivateSkill: (qualifiedName: string) => Promise<void>;
  onOpenTool: (tool: "terminal") => void;
  onSend: (
    content: string,
    interactionId?: string,
    recipients?: string[],
    dispatchKind?: string,
  ) => Promise<void>;
}

function EventIcon({ event }: { event: ActivityEvent }) {
  if (event.actor.kind === "developer") return <UserCircle weight="fill" />;
  if (event.type.startsWith("delivery.") || event.capture_grade === "verified") {
    return <ShieldCheck />;
  }
  if (event.type.startsWith("assignment.")) return <Robot />;
  return <GitBranch />;
}

function TimelineEvent({ event }: { event: ActivityEvent }) {
  const detail = eventDetail(event);
  return (
    <article className={`timeline-event grade-${event.capture_grade}`}>
      <span className="event-icon" aria-hidden="true">
        <EventIcon event={event} />
      </span>
      <div>
        <header>
          <strong>{eventTitle(event)}</strong>
          <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
        </header>
        {detail ? <p>{detail}</p> : null}
        <footer>
          <span>{labelActor(event.actor.id)}</span>
          <span>{event.capture_grade === "verified" ? "已验证" : event.capture_grade === "observed" ? "已观察" : "已记录"}</span>
        </footer>
      </div>
    </article>
  );
}

type TimelineGroup =
  | { kind: "event"; key: string; events: [ActivityEvent] }
  | { kind: "event-group"; key: string; actorKey: string; events: ActivityEvent[] };

const humanInputEvents = new Set([
  "user.message",
  "interaction.responded",
  "interaction.answer_received",
  "review.changes_requested",
  "delivery.accepted",
  "delivery.answered",
  "workspace.rolled_back",
  "conversation.mode_selected",
  "orchestration.plan_approved",
]);

function isHumanInput(event: ActivityEvent): boolean {
  const sourceType =
    event.type === "run.event" && typeof event.payload.source_type === "string"
      ? event.payload.source_type
      : "";
  return (
    humanInputEvents.has(event.type) ||
    sourceType === "interaction.responded"
  );
}

export function groupTimelineEvents(events: ActivityEvent[]): TimelineGroup[] {
  const result: TimelineGroup[] = [];
  let pending: ActivityEvent[] = [];
  let pendingActor = "";
  const flush = () => {
    if (!pending.length) return;
    if (pending.length >= 2) {
      result.push({
        kind: "event-group",
        key: `event-group:${pendingActor}:${pending[0].event_id}`,
        actorKey: pendingActor,
        events: pending,
      });
    } else {
      result.push(
        ...pending.map(
          (event): TimelineGroup => ({
            kind: "event",
            key: event.event_id,
            events: [event],
          }),
        ),
      );
    }
    pending = [];
    pendingActor = "";
  };
  for (const event of events) {
    if (isHumanInput(event)) {
      flush();
      result.push({ kind: "event", key: event.event_id, events: [event] });
      continue;
    }
    const actorKey = `${event.actor.kind}:${event.actor.id}`;
    if (pending.length && pendingActor !== actorKey) flush();
    pendingActor = actorKey;
    pending.push(event);
  }
  flush();
  return result;
}

function TimelineEventGroup({
  group,
  expanded,
  onToggle,
}: {
  group: Extract<TimelineGroup, { kind: "event-group" }>;
  expanded: boolean;
  onToggle: () => void;
}) {
  const first = group.events[0];
  const last = group.events.at(-1)!;
  const actors = Array.from(
    new Set(group.events.map((event) => labelActor(event.actor.id))),
  );
  const grades = Array.from(
    new Set(
      group.events.map((event) =>
        event.capture_grade === "verified"
          ? "已验证"
          : event.capture_grade === "observed"
            ? "已观察"
            : "已记录",
      ),
    ),
  );
  const typeCounts = Array.from(
    group.events.reduce((counts, event) => {
      const key = eventSemanticType(event);
      counts.set(key, (counts.get(key) ?? 0) + 1);
      return counts;
    }, new Map<string, number>()),
  )
    .map(([semanticType, count]) => {
      const example = group.events.find(
        (event) => eventSemanticType(event) === semanticType,
      );
      return `${example ? eventTitle(example) : semanticType} ${count}`;
    })
    .join(" · ");
  const latestDetail = eventDetail(last);
  return (
    <section className={`timeline-event-group ${expanded ? "expanded" : "collapsed"}`}>
      <button
        className="timeline-group-toggle"
        type="button"
        aria-expanded={expanded}
        aria-controls={`${group.key}-events`}
        onClick={onToggle}
      >
        <CaretDown aria-hidden="true" />
        <span className="timeline-group-summary">
          <strong>
            {labelActor(last.actor.id)} · 连续 {group.events.length} 条活动
          </strong>
          <span>{typeCounts}</span>
          <small>{eventExplanation(last)}</small>
          {latestDetail ? <small>最新结果：{latestDetail}</small> : null}
          <small>
            {actors.join("、")} · {formatTime(first.occurred_at)}–
            {formatTime(last.occurred_at)} · {grades.join(" / ")}
          </small>
        </span>
      </button>
      <div id={`${group.key}-events`}>
        {(expanded ? group.events : [last]).map((event) => (
          <TimelineEvent event={event} key={event.event_id} />
        ))}
      </div>
    </section>
  );
}

function AssignmentFleet({ assignments }: { assignments: Assignment[] }) {
  if (!assignments.length) return null;
  return (
    <section className="fleet-card" aria-labelledby="fleet-title">
      <header>
        <div>
          <span className="eyebrow">VISIBLE FLEET</span>
          <h2 id="fleet-title">协作进度</h2>
        </div>
        <span>
          {assignments.filter((item) => item.status === "running").length} 个运行中
        </span>
      </header>
      <div className="fleet-grid">
        {assignments.map((assignment) => {
          const metadata =
            assignment.metadata && typeof assignment.metadata === "object"
              ? assignment.metadata
              : {};
          const waitingReason =
            metadata.waiting_reason &&
            typeof metadata.waiting_reason === "object"
              ? (metadata.waiting_reason as Record<string, unknown>)
              : null;
          return (
          <article key={assignment.assignment_id}>
            <span className={`fleet-state ${assignment.status}`} aria-hidden="true">
              {assignment.status === "running" ? (
                <CircleNotch className="spin" />
              ) : assignment.status === "completed" ? (
                <CheckCircle />
              ) : (
                <Robot />
              )}
            </span>
            <div>
              <strong>{assignment.title}</strong>
              <small>
                {assignment.agent_id} · {labelStatus(assignment.status)}
              </small>
              {waitingReason?.message ? (
                <p className="fleet-reason">{String(waitingReason.message)}</p>
              ) : null}
            </div>
          </article>
          );
        })}
      </div>
    </section>
  );
}

interface ExecutionDagNode {
  id: string;
  title: string;
  subtitle: string;
  status: string;
  dependencies: string[];
  events: ActivityEvent[];
  detail: string;
}

interface PositionedDagNode extends ExecutionDagNode {
  x: number;
  y: number;
}

const DAG_NODE_WIDTH = 176;
const DAG_NODE_HEIGHT = 72;
const DAG_COLUMN_GAP = 58;
const DAG_ROW_GAP = 18;

function layoutDag(nodes: ExecutionDagNode[]): {
  nodes: PositionedDagNode[];
  width: number;
  height: number;
} {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const ranks = new Map<string, number>();
  const visiting = new Set<string>();
  const rankFor = (id: string): number => {
    if (ranks.has(id)) return ranks.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const node = byId.get(id);
    const rank = node?.dependencies.length
      ? Math.max(
          0,
          ...node.dependencies
            .filter((dependency) => byId.has(dependency))
            .map((dependency) => rankFor(dependency) + 1),
        )
      : 0;
    visiting.delete(id);
    ranks.set(id, rank);
    return rank;
  };
  nodes.forEach((node) => rankFor(node.id));
  const columns = new Map<number, ExecutionDagNode[]>();
  nodes.forEach((node) => {
    const rank = ranks.get(node.id) ?? 0;
    columns.set(rank, [...(columns.get(rank) ?? []), node]);
  });
  const maxRows = Math.max(1, ...Array.from(columns.values()).map((items) => items.length));
  const positioned: PositionedDagNode[] = [];
  Array.from(columns.entries())
    .sort(([left], [right]) => left - right)
    .forEach(([rank, items]) => {
      const columnHeight =
        items.length * DAG_NODE_HEIGHT + Math.max(0, items.length - 1) * DAG_ROW_GAP;
      const fullHeight =
        maxRows * DAG_NODE_HEIGHT + Math.max(0, maxRows - 1) * DAG_ROW_GAP;
      const offset = Math.max(0, (fullHeight - columnHeight) / 2);
      items.forEach((node, row) => {
        positioned.push({
          ...node,
          x: 16 + rank * (DAG_NODE_WIDTH + DAG_COLUMN_GAP),
          y: 16 + offset + row * (DAG_NODE_HEIGHT + DAG_ROW_GAP),
        });
      });
    });
  const maxRank = Math.max(0, ...Array.from(ranks.values()));
  return {
    nodes: positioned,
    width: 32 + (maxRank + 1) * DAG_NODE_WIDTH + maxRank * DAG_COLUMN_GAP,
    height:
      32 + maxRows * DAG_NODE_HEIGHT + Math.max(0, maxRows - 1) * DAG_ROW_GAP,
  };
}

function eventAssignmentId(event: ActivityEvent): string {
  return String(
    event.source.assignment_id ??
      (typeof event.payload.assignment_id === "string"
        ? event.payload.assignment_id
        : ""),
  );
}

function eventsForAssignment(
  snapshot: ConversationSnapshot,
  assignment?: Assignment,
): ActivityEvent[] {
  if (!assignment) return snapshot.timeline;
  return snapshot.timeline.filter(
    (event) =>
      eventAssignmentId(event) === assignment.assignment_id ||
      (assignment.run_id && event.source.run_id === assignment.run_id),
  );
}

function multiAgentNodes(snapshot: ConversationSnapshot): ExecutionDagNode[] {
  if (snapshot.assignments.length) {
    return snapshot.assignments.map((assignment) => {
      const session = snapshot.sessions.find(
        (item) => item.current_assignment_id === assignment.assignment_id,
      );
      const events = eventsForAssignment(snapshot, assignment);
      return {
        id: assignment.assignment_id,
        title: assignment.title,
        subtitle: `${assignment.agent_id}${
          session ? ` · Generation ${session.generation}` : ""
        }`,
        status: assignment.status,
        dependencies: assignment.dependencies ?? [],
        events,
        detail: assignment.dependencies?.length
          ? `等待 ${assignment.dependencies.length} 个上游任务完成`
          : "没有上游依赖，可以直接调度",
      };
    });
  }
  const plan = snapshot.orchestration_plans.at(-1);
  return (
    plan?.plan.nodes.map((node) => ({
      id: node.id,
      title: node.title,
      subtitle: node.agent_id,
      status: plan.status === "completed" ? "completed" : "proposed",
      dependencies: node.dependencies,
      events: [],
      detail: node.dependencies.length
        ? `依赖 ${node.dependencies.join("、")}`
        : "没有上游依赖，可以直接调度",
    })) ?? []
  );
}

type SinglePhase =
  | "created"
  | "prepared"
  | "executing"
  | "reported"
  | "verifying"
  | "integrated"
  | "terminal";

const phaseLabels: Record<SinglePhase, string> = {
  created: "任务已创建",
  prepared: "工作区与 Session 已准备",
  executing: "Agent 正在执行",
  reported: "Agent 已回报",
  verifying: "正在验证交付",
  integrated: "变更已合并",
  terminal: "任务已结算",
};

function phaseForEvent(event: ActivityEvent): SinglePhase | null {
  if (event.type === "assignment.created") return "created";
  if (
    event.type === "workspace.prepared" ||
    event.type === "workspace.baseline_captured" ||
    event.type === "provider.session_bound"
  ) {
    return "prepared";
  }
  if (
    event.type === "assignment.started" ||
    event.type === "skill.loaded" ||
    event.type === "file.changed" ||
    event.type === "run.requested" ||
    event.type === "run.event"
  ) {
    return "executing";
  }
  if (event.type === "assignment.reported") return "reported";
  if (
    event.type.startsWith("verification.") ||
    event.type.startsWith("delivery.") ||
    event.type === "workspace.reconciled"
  ) {
    return "verifying";
  }
  if (event.type === "assignment.merged" || event.type === "orchestration.completed") {
    return "integrated";
  }
  if (
    event.type.includes("failed") ||
    event.type.includes("blocked") ||
    event.type === "assignment.cancelled"
  ) {
    return "terminal";
  }
  return null;
}

function singleAgentNodes(
  snapshot: ConversationSnapshot,
  assignment?: Assignment,
): ExecutionDagNode[] {
  const events = eventsForAssignment(snapshot, assignment);
  const grouped = new Map<
    string,
    { phase: SinglePhase; generation?: number | null; events: ActivityEvent[] }
  >();
  for (const event of events) {
    const phase = phaseForEvent(event);
    if (!phase) continue;
    const generation =
      phase === "executing" ? event.source.generation ?? 1 : null;
    const key = phase === "executing" ? `${phase}:${generation}` : phase;
    const current = grouped.get(key) ?? { phase, generation, events: [] };
    current.events.push(event);
    grouped.set(key, current);
  }
  if (!grouped.size) {
    grouped.set("created", { phase: "created", events: [] });
  }
  const items = Array.from(grouped.entries()).sort(
    ([, left], [, right]) =>
      (left.events[0]?.sequence ?? 0) - (right.events[0]?.sequence ?? 0),
  );
  const terminalStates = new Set([
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "waiting_user",
  ]);
  if (
    assignment &&
    terminalStates.has(assignment.status) &&
    !items.some(([, item]) => item.phase === "terminal" || item.phase === "integrated")
  ) {
    items.push([
      "terminal",
      { phase: "terminal", events: [] },
    ]);
  }
  return items.map(([key, item], index) => {
    const latest = item.events.at(-1);
    const isLast = index === items.length - 1;
    const generationLabel =
      item.phase === "executing" && item.generation
        ? ` · Generation ${item.generation}`
        : "";
    return {
      id: key,
      title: phaseLabels[item.phase],
      subtitle: `${item.events.length} 条事件${generationLabel}`,
      status:
        isLast && assignment
          ? assignment.status
          : item.phase === "terminal" && assignment
            ? assignment.status
            : "completed",
      dependencies: index ? [items[index - 1][0]] : [],
      events: item.events,
      detail: latest
        ? eventExplanation(latest)
        : assignment
          ? `当前 Assignment 状态：${labelStatus(assignment.status)}`
          : "会话已经创建，等待生成可追踪的执行阶段。",
    };
  });
}

function DagCanvas({
  graph,
  selectedId,
  onSelect,
}: {
  graph: ReturnType<typeof layoutDag>;
  selectedId?: string;
  onSelect: (node: ExecutionDagNode) => void;
}) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  return (
    <div className="execution-dag-scroll" tabIndex={0} aria-label="Agent 执行 DAG">
      <div
        className="execution-dag-canvas"
        style={{ width: graph.width, height: graph.height }}
      >
        <svg
          width={graph.width}
          height={graph.height}
          viewBox={`0 0 ${graph.width} ${graph.height}`}
          aria-hidden="true"
        >
          <defs>
            <marker
              id="execution-dag-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 Z" />
            </marker>
          </defs>
          {graph.nodes.flatMap((node) =>
            node.dependencies.map((dependency) => {
              const source = byId.get(dependency);
              if (!source) return null;
              const startX = source.x + DAG_NODE_WIDTH;
              const startY = source.y + DAG_NODE_HEIGHT / 2;
              const endX = node.x;
              const endY = node.y + DAG_NODE_HEIGHT / 2;
              const control = Math.max(24, (endX - startX) / 2);
              return (
                <path
                  className="execution-dag-edge"
                  key={`${dependency}->${node.id}`}
                  d={`M ${startX} ${startY} C ${startX + control} ${startY}, ${
                    endX - control
                  } ${endY}, ${endX} ${endY}`}
                  markerEnd="url(#execution-dag-arrow)"
                />
              );
            }),
          )}
        </svg>
        {graph.nodes.map((node) => (
          <button
            className={`execution-dag-node status-${node.status}${
              selectedId === node.id ? " selected" : ""
            }`}
            type="button"
            style={{ left: node.x, top: node.y }}
            key={node.id}
            aria-pressed={selectedId === node.id}
            onClick={() => onSelect(node)}
          >
            <span className={`state-dot ${node.status}`} aria-hidden="true" />
            <span>
              <strong>{node.title}</strong>
              <small>{node.subtitle}</small>
            </span>
            <em>{labelStatus(node.status)}</em>
          </button>
        ))}
      </div>
    </div>
  );
}

function ExecutionDag({ snapshot }: { snapshot: ConversationSnapshot }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"multi" | "single">(
    snapshot.assignments.length > 1 ? "multi" : "single",
  );
  const [assignmentId, setAssignmentId] = useState(
    snapshot.assignments[0]?.assignment_id ?? "",
  );
  const [selectedNode, setSelectedNode] = useState<ExecutionDagNode | null>(null);
  const assignment =
    snapshot.assignments.find((item) => item.assignment_id === assignmentId) ??
    snapshot.assignments[0];
  const nodes = useMemo(
    () =>
      mode === "multi"
        ? multiAgentNodes(snapshot)
        : singleAgentNodes(snapshot, assignment),
    [assignment, mode, snapshot],
  );
  const graph = useMemo(() => layoutDag(nodes), [nodes]);

  useEffect(() => {
    if (
      snapshot.assignments.length &&
      !snapshot.assignments.some((item) => item.assignment_id === assignmentId)
    ) {
      setAssignmentId(snapshot.assignments[0].assignment_id);
    }
  }, [assignmentId, snapshot.assignments]);

  useEffect(() => {
    if (!selectedNode) return;
    setSelectedNode(nodes.find((node) => node.id === selectedNode.id) ?? null);
  }, [nodes, selectedNode]);

  if (!nodes.length && !snapshot.assignments.length) return null;
  return (
    <section className="execution-dag-card" aria-labelledby="execution-dag-title">
      <button
        className="execution-dag-toggle"
        type="button"
        aria-expanded={open}
        aria-controls="execution-dag-content"
        onClick={() => setOpen((current) => !current)}
      >
        <span>
          <span className="eyebrow">EXECUTION GRAPH</span>
          <strong id="execution-dag-title">Agent 执行流程</strong>
          <small>
            {snapshot.assignments.length > 1
              ? `${snapshot.assignments.length} 个任务 · ${snapshot.assignments.filter((item) => item.status === "running").length} 个运行中`
              : `${assignment?.agent_id || snapshot.conversation.primary_agent_id || "Agent"} · ${labelStatus(assignment?.status || snapshot.conversation.status)}`}
          </small>
        </span>
        <CaretDown aria-hidden="true" />
      </button>
      {open ? (
        <div id="execution-dag-content" className="execution-dag-content">
          <div className="execution-dag-toolbar">
            <div className="segmented" aria-label="执行图模式">
              <button
                type="button"
                className={mode === "multi" ? "active" : ""}
                onClick={() => {
                  setMode("multi");
                  setSelectedNode(null);
                }}
                disabled={snapshot.assignments.length < 2}
              >
                多 Agent
              </button>
              <button
                type="button"
                className={mode === "single" ? "active" : ""}
                onClick={() => {
                  setMode("single");
                  setSelectedNode(null);
                }}
              >
                单 Agent
              </button>
            </div>
            {mode === "single" && snapshot.assignments.length > 1 ? (
              <select
                aria-label="选择 Agent 任务"
                value={assignment?.assignment_id}
                onChange={(event) => {
                  setAssignmentId(event.target.value);
                  setSelectedNode(null);
                }}
              >
                {snapshot.assignments.map((item) => (
                  <option value={item.assignment_id} key={item.assignment_id}>
                    {item.agent_id} · {item.title}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          <DagCanvas
            graph={graph}
            selectedId={selectedNode?.id}
            onSelect={setSelectedNode}
          />
          {selectedNode ? (
            <aside className="execution-node-inspector" aria-live="polite">
              <header>
                <div>
                  <span className="eyebrow">NODE INSPECTOR</span>
                  <h3>{selectedNode.title}</h3>
                </div>
                <span className={`status-pill ${selectedNode.status}`}>
                  {labelStatus(selectedNode.status)}
                </span>
              </header>
              <p>{selectedNode.detail}</p>
              <small>
                {selectedNode.dependencies.length
                  ? `依赖：${selectedNode.dependencies.join("、")}`
                  : "无上游依赖"}
                {" · "}
                {selectedNode.events.length} 条关联事件
              </small>
              {selectedNode.events.length ? (
                <ul>
                  {selectedNode.events.slice(-8).map((event) => (
                    <li key={event.event_id}>
                      <time>{formatTime(event.occurred_at)}</time>
                      <span>
                        <strong>{eventTitle(event)}</strong>
                        <small>{eventDetail(event) || eventExplanation(event)}</small>
                      </span>
                      <em>{event.capture_grade}</em>
                    </li>
                  ))}
                </ul>
              ) : null}
            </aside>
          ) : (
            <p className="execution-dag-help">选择节点查看依赖、事件和最新结果。</p>
          )}
        </div>
      ) : null}
    </section>
  );
}

export function ConversationTimeline({
  snapshot,
  sending,
  skills,
  onActivateSkill,
  onOpenTool,
  onSend,
}: Props) {
  const [message, setMessage] = useState("");
  const [target, setTarget] = useState("primary");
  const [dispatchKind, setDispatchKind] = useState("message");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const timelineRef = useRef<HTMLDivElement>(null);
  const groupedTimeline = useMemo(
    () => groupTimelineEvents(snapshot.timeline),
    [snapshot.timeline],
  );
  const pendingInteraction = snapshot.interactions.find(
    (item) => item.status === "pending",
  );
  const runtimeAttention =
    !pendingInteraction && snapshot.attention_detail?.action === "open_terminal"
      ? snapshot.attention_detail
      : null;
  const skillMatches = useMemo(() => {
    if (!message.startsWith("/skill") || pendingInteraction) return [];
    const query = message.replace(/^\/skill:?/, "").trim().toLocaleLowerCase();
    return (skills?.catalog ?? [])
      .filter((item) => item.enabled)
      .filter(
        (item) =>
          !query ||
          item.qualified_name.toLocaleLowerCase().includes(query) ||
          item.description.toLocaleLowerCase().includes(query),
      )
      .slice(0, 8);
  }, [message, pendingInteraction, skills?.catalog]);

  useEffect(() => {
    timelineRef.current?.scrollTo({
      top: timelineRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [snapshot.timeline.length]);

  async function submit() {
    let value = message.trim();
    if (!value || sending) return;
    try {
      const invocation = pendingInteraction
        ? null
        : value.match(/^\/skill:([^\s]+)\s*(.*)$/s);
      if (invocation) {
        await onActivateSkill(invocation[1]);
        value = invocation[2].trim();
        if (!value) {
          setMessage("");
          return;
        }
      }
      const participantIds = snapshot.participants
        .map((item) => String(item.agent_id || ""))
        .filter(Boolean);
      const recipients =
        target === "team"
          ? participantIds
          : target.startsWith("agent:")
            ? [target.slice(6)]
            : [snapshot.conversation.primary_agent_id || ""].filter(Boolean);
      await onSend(
        value,
        pendingInteraction?.interaction_id,
        recipients,
        pendingInteraction ? "message" : dispatchKind,
      );
      setMessage("");
    } catch {
      // The mutation renders the structured error; keep the draft for retry.
    }
  }

  return (
    <main className="conversation-main">
      <div className="timeline-scroll" ref={timelineRef}>
        <section className="conversation-intro">
          <span className="eyebrow">DELIVERY CONTRACT</span>
          <p>{snapshot.conversation.goal}</p>
        </section>
        {pendingInteraction ? (
          <section className="interaction-card">
            <span className="interaction-icon" aria-hidden="true">
              <UserCircle weight="fill" />
            </span>
            <div>
              <span className="eyebrow">需要你确认</span>
              <h2>{pendingInteraction.prompt}</h2>
              <p>你的回答会进入冻结的交付要求，并继续复用当前逻辑 Session。</p>
              {pendingInteraction.options?.length ? (
                <div className="interaction-options">
                  {pendingInteraction.options.map((option) => (
                    <button
                      className={option.recommended ? "primary" : "secondary"}
                      type="button"
                      key={option.value || option.label}
                      disabled={sending}
                      onClick={async () => {
                        try {
                          await onSend(
                            option.value || option.label,
                            pendingInteraction.interaction_id,
                          );
                        } catch {
                          // The owning mutation preserves the custom draft.
                        }
                      }}
                    >
                      <span>{option.label}</span>
                      {option.description ? <small>{option.description}</small> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        ) : null}
        {runtimeAttention ? (
          <section className="interaction-card runtime-attention-card" role="alert">
            <span className="interaction-icon" aria-hidden="true">
              <Warning weight="fill" />
            </span>
            <div>
              <span className="eyebrow">{runtimeAttention.label}</span>
              <h2>{runtimeAttention.title}</h2>
              <p>{runtimeAttention.message}</p>
              <p className="attention-remediation">
                {runtimeAttention.remediation}
              </p>
              <button
                className="primary"
                type="button"
                onClick={() => onOpenTool("terminal")}
              >
                <TerminalWindow aria-hidden="true" />
                打开 Terminal 查看并处理
              </button>
            </div>
          </section>
        ) : null}
        <ExecutionDag snapshot={snapshot} />
        <AssignmentFleet assignments={snapshot.assignments} />
        <section className="timeline-list" aria-label="活动时间线" aria-live="polite">
          {groupedTimeline.map((group) =>
            group.kind === "event-group" ? (
              <TimelineEventGroup
                group={group}
                key={group.key}
                expanded={expandedGroups.has(group.key)}
                onToggle={() =>
                  setExpandedGroups((current) => {
                    const next = new Set(current);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  })
                }
              />
            ) : (
              <TimelineEvent event={group.events[0]} key={group.key} />
            ),
          )}
          {!snapshot.timeline.length ? (
            <div className="empty-state">活动会从这里开始，并按证据等级持续记录。</div>
          ) : null}
        </section>
      </div>
      <section id="composer" className="composer">
        <label>
          <span className="sr-only">
            {pendingInteraction ? "自定义回答" : "发送消息"}
          </span>
          <textarea
            aria-label={pendingInteraction ? "自定义回答" : "发送消息"}
            rows={2}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder={
              pendingInteraction
                ? "回答这个问题，Enter 提交…"
                : runtimeAttention
                  ? "可继续补充上下文；当前阻塞需先在 Terminal 处理…"
                : "继续补充上下文、调整方向或请求新一轮工作…"
            }
          />
        </label>
        {skillMatches.length ? (
          <div className="skill-picker" role="listbox" aria-label="可用 Skills">
            {skillMatches.map((skill) => (
              <button
                type="button"
                role="option"
                aria-selected="false"
                key={skill.qualified_name}
                onClick={() => setMessage(`/skill:${skill.qualified_name} `)}
              >
                <PuzzlePiece aria-hidden="true" />
                <span>
                  <strong>{skill.qualified_name}</strong>
                  <small>{skill.description || skill.source_id}</small>
                </span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="composer-meta">
          {!pendingInteraction ? (
            <div className="composer-routing">
              <select
                aria-label="消息接收者"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              >
                <option value="primary">
                  主 Agent · {snapshot.conversation.primary_agent_id || "Agent"}
                </option>
                {snapshot.participants.length > 1 ? (
                  <option value="team">全部协作 Agent</option>
                ) : null}
                {snapshot.participants
                  .filter(
                    (item) =>
                      String(item.agent_id || "") !==
                      snapshot.conversation.primary_agent_id,
                  )
                  .map((item) => (
                    <option
                      key={String(item.agent_id)}
                      value={`agent:${String(item.agent_id)}`}
                    >
                      {String(item.display_name || item.agent_id)}
                    </option>
                  ))}
              </select>
              <select
                aria-label="派发方式"
                value={dispatchKind}
                onChange={(event) => setDispatchKind(event.target.value)}
              >
                <option value="message">补充上下文</option>
                <option value="consult">创建咨询任务</option>
                <option value="write">创建修改任务</option>
                <option value="review">创建复核任务</option>
              </select>
            </div>
          ) : (
            <span>回答会写入不可变 Review 记录</span>
          )}
          <button
            className="send-button"
            type="button"
            onClick={() => void submit()}
            disabled={sending || !message.trim()}
            aria-label="发送"
          >
            {sending ? <CircleNotch className="spin" /> : <ArrowUp weight="bold" />}
          </button>
        </div>
      </section>
    </main>
  );
}
