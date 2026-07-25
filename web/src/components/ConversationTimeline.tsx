import {
  ArrowUp,
  CheckCircle,
  CircleNotch,
  GitBranch,
  Robot,
  ShieldCheck,
  UserCircle,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import {
  eventDetail,
  eventTitle,
  formatTime,
  labelActor,
  labelStatus,
} from "../labels";
import type {
  ActivityEvent,
  Assignment,
  ConversationSnapshot,
} from "../types";

interface Props {
  snapshot: ConversationSnapshot;
  sending: boolean;
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
        {assignments.map((assignment) => (
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
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function OrchestrationPlan({ snapshot }: { snapshot: ConversationSnapshot }) {
  const plan = (snapshot.orchestration_plans ?? []).at(-1);
  if (!plan) return null;
  return (
    <section className="orchestration-card" aria-labelledby="orchestration-title">
      <header>
        <div>
          <span className="eyebrow">ORCHESTRATION PLAN · V{plan.version}</span>
          <h2 id="orchestration-title">{plan.summary}</h2>
        </div>
        <span>{labelStatus(plan.status)}</span>
      </header>
      <div className="dag-list">
        {plan.plan.nodes.map((node) => (
          <article key={node.id}>
            <span className="dag-node-id">{node.id}</span>
            <div>
              <strong>{node.title}</strong>
              <small>
                {node.agent_id}
                {" · "}
                {node.executor_kind === "native_subagent"
                  ? "CLI 原生子任务"
                  : "独立 Agent Session"}
              </small>
            </div>
            <span className="dag-dependencies">
              {node.dependencies.length
                ? `依赖 ${node.dependencies.join("、")}`
                : "可立即执行"}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ConversationTimeline({ snapshot, sending, onSend }: Props) {
  const [message, setMessage] = useState("");
  const [target, setTarget] = useState("primary");
  const [dispatchKind, setDispatchKind] = useState("message");
  const timelineRef = useRef<HTMLDivElement>(null);
  const pendingInteraction = snapshot.interactions.find(
    (item) => item.status === "pending",
  );

  useEffect(() => {
    timelineRef.current?.scrollTo({
      top: timelineRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [snapshot.timeline.length]);

  async function submit() {
    const value = message.trim();
    if (!value || sending) return;
    try {
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
        <OrchestrationPlan snapshot={snapshot} />
        <AssignmentFleet assignments={snapshot.assignments} />
        <section className="timeline-list" aria-label="活动时间线" aria-live="polite">
          {snapshot.timeline.map((event) => (
            <TimelineEvent event={event} key={event.event_id} />
          ))}
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
                : "继续补充上下文、调整方向或请求新一轮工作…"
            }
          />
        </label>
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
