import { Plus, X } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentDefinition, RuleDefinition } from "../types";

interface Props {
  open: boolean;
  agents: AgentDefinition[];
  rules: RuleDefinition[];
  busy: boolean;
  returnFocus?: HTMLElement | null;
  onClose: () => void;
  onSubmit: (value: {
    goal: string;
    title?: string;
    mode: "direct" | "orchestrated";
    agent_id: string;
    deliverables: Array<Record<string, string>>;
    profile: "lite" | "standard" | "strict";
    auto_start_when_ready: boolean;
    collaborator_agent_ids: string[];
    rule_ids: string[];
  }) => void;
}

export function NewConversationDialog({
  open,
  agents,
  rules,
  busy,
  returnFocus,
  onClose,
  onSubmit,
}: Props) {
  const [goal, setGoal] = useState("");
  const [agent, setAgent] = useState("");
  const [mode, setMode] = useState<"direct" | "orchestrated">("direct");
  const [deliverable, setDeliverable] = useState("code_change");
  const [profile, setProfile] = useState<"lite" | "standard" | "strict">("standard");
  const [collaborators, setCollaborators] = useState<string[]>([]);
  const [selectedRules, setSelectedRules] = useState<string[]>([]);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  const primaryAgents = useMemo(
    () => agents.filter((item) => item.capability_tags?.includes("code") ?? true),
    [agents],
  );
  const availableAgents = useMemo(
    () =>
      primaryAgents
        .filter(
          (item) =>
            item.enabled &&
            item.available &&
            (mode === "direct" || item.can_orchestrate),
        )
        .sort(compareAgents),
    [primaryAgents, mode],
  );
  const unavailableAgents = useMemo(
    () =>
      primaryAgents
        .filter(
          (item) =>
            item.enabled &&
            (!item.available ||
              (mode === "orchestrated" && !item.can_orchestrate)),
        )
        .sort(compareAgents),
    [primaryAgents, mode],
  );
  const usableAgents = useMemo(
    () => availableAgents,
    [availableAgents],
  );
  const selectedAgent = primaryAgents.find((item) => item.agent_id === agent);

  useEffect(() => {
    if (!usableAgents.some((item) => item.agent_id === agent)) {
      setAgent(usableAgents[0]?.agent_id ?? "");
    }
  }, [agent, usableAgents]);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) {
      setGoal("");
      setMode("direct");
      setDeliverable("code_change");
      setProfile("standard");
      setCollaborators([]);
      setSelectedRules([]);
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((item) => item.offsetParent !== null);
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      returnFocus?.focus();
    };
  }, [open, returnFocus]);

  if (!open) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="new-conversation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-conversation-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">NEW SESSION</span>
            <h2 id="new-conversation-title">把目标交给可信交付闭环</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭">
            <X />
          </button>
        </header>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (
              !goal.trim() ||
              !usableAgents.some((item) => item.agent_id === agent)
            ) {
              return;
            }
            onSubmit({
              goal: goal.trim(),
              mode,
              agent_id: agent,
              deliverables: [{ type: deliverable }],
              profile,
              auto_start_when_ready: true,
              collaborator_agent_ids: collaborators,
              rule_ids: selectedRules,
            });
          }}
        >
          <label>
            <span>任务目标</span>
            <textarea
              autoFocus
              required
              rows={5}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="描述要解决的问题、期望结果和必要约束…"
            />
          </label>
          <div className="form-grid">
            <label>
              <span>主要 Agent</span>
              <select
                value={agent}
                onChange={(event) => setAgent(event.target.value)}
                aria-describedby="agent-availability"
              >
                {availableAgents.length ? (
                  <optgroup label={`已检测到（${availableAgents.length}）`}>
                    {availableAgents.map((item) => (
                      <option key={item.agent_id} value={item.agent_id}>
                        {item.display_name || item.agent_id}
                        {item.detection === "builtin" ? "（内置）" : ""}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {unavailableAgents.length ? (
                  <optgroup label={`未就绪（${unavailableAgents.length}）`}>
                    {unavailableAgents.map((item) => (
                      <option key={item.agent_id} value={item.agent_id} disabled>
                        {item.display_name || item.agent_id}
                        {item.available
                          ? "（不支持编排）"
                          : item.availability_code === "pty_unavailable"
                            ? "（需要 PTY）"
                            : "（未检测到）"}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
              <small id="agent-availability" className="field-help">
                {selectedAgent
                  ? selectedAgent.availability_reason
                  : "当前没有可用于该协作方式的 Agent。"}
              </small>
              <p className="agent-detection-summary">
                自动检测到 {availableAgents.length} 个可用 Agent
                {unavailableAgents.length
                  ? `，${unavailableAgents.length} 个尚未就绪`
                  : ""}
                。
              </p>
              {unavailableAgents.length ? (
                <details className="agent-remediation">
                  <summary>查看未就绪 Agent</summary>
                  <div>
                    {unavailableAgents.map((item) => (
                      <p key={item.agent_id}>
                        <strong>{item.display_name || item.agent_id}：</strong>
                        {item.available
                          ? "当前 Agent 不支持多 Agent 编排"
                          : item.availability_reason}
                        {item.remediation ? `；${item.remediation}` : ""}
                      </p>
                    ))}
                  </div>
                </details>
              ) : null}
            </label>
            <label>
              <span>协作方式</span>
              <select
                value={mode}
                onChange={(event) =>
                  setMode(event.target.value as "direct" | "orchestrated")
                }
              >
                <option value="direct">单 Agent 直达</option>
                <option value="orchestrated">多 Agent 编排</option>
              </select>
            </label>
            <label>
              <span>最终想拿到什么</span>
              <select
                value={deliverable}
                onChange={(event) => setDeliverable(event.target.value)}
              >
                <option value="code_change">代码变更</option>
                <option value="answer">分析回答</option>
                <option value="report">结构化报告</option>
                <option value="file">指定文件</option>
                <option value="runnable">可运行结果</option>
              </select>
            </label>
            <label>
              <span>交付强度</span>
              <select
                value={profile}
                onChange={(event) =>
                  setProfile(event.target.value as "lite" | "standard" | "strict")
                }
              >
                <option value="lite">Lite</option>
                <option value="standard">Standard</option>
                <option value="strict">Strict</option>
              </select>
            </label>
          </div>
          <section className="conversation-team-picker">
            <div>
              <span>协作 Agent</span>
              <small>每个选中的 Agent 都会建立独立的交互式 Session。</small>
            </div>
            <div className="choice-grid">
              {agents
                .filter((item) => item.available && item.agent_id !== agent)
                .map((item) => (
                  <label key={item.agent_id}>
                    <input
                      type="checkbox"
                      checked={collaborators.includes(item.agent_id)}
                      onChange={(event) =>
                        setCollaborators((current) =>
                          event.target.checked
                            ? [...current, item.agent_id]
                            : current.filter((value) => value !== item.agent_id),
                        )
                      }
                    />
                    <span>{item.display_name || item.agent_id}</span>
                  </label>
                ))}
              {!agents.some((item) => item.available && item.agent_id !== agent) ? (
                <p>暂无其他可用 Agent；任务会由主 Agent 串行执行。</p>
              ) : null}
            </div>
          </section>
          {rules.length ? (
            <section className="conversation-team-picker">
              <div>
                <span>可信交付 Rule</span>
                <small>创建时冻结具体版本，运行中不会被全局修改覆盖。</small>
              </div>
              <div className="choice-grid">
                {rules.map((rule) => (
                  <label key={`${rule.rule_id}@${rule.version}`}>
                    <input
                      type="checkbox"
                      checked={selectedRules.includes(rule.rule_id)}
                      onChange={(event) =>
                        setSelectedRules((current) =>
                          event.target.checked
                            ? [...current, rule.rule_id]
                            : current.filter((value) => value !== rule.rule_id),
                        )
                      }
                    />
                    <span>
                      {rule.title} · v{rule.version}
                    </span>
                  </label>
                ))}
              </div>
            </section>
          ) : null}
          <details className="advanced">
            <summary>高级范围与验收条件</summary>
            <p>创建后可在 Conversation 中继续补充范围、交付标准与验证要求。</p>
          </details>
          <footer>
            <button className="secondary" type="button" onClick={onClose}>
              取消
            </button>
            <button
              className="primary"
              type="submit"
              disabled={busy || !goal.trim() || !agent}
            >
              <Plus weight="bold" />
              {busy ? "正在创建…" : "创建任务"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function compareAgents(left: AgentDefinition, right: AgentDefinition): number {
  const leftBuiltin = left.detection === "builtin" ? 1 : 0;
  const rightBuiltin = right.detection === "builtin" ? 1 : 0;
  if (leftBuiltin !== rightBuiltin) return leftBuiltin - rightBuiltin;
  return (left.display_name || left.agent_id).localeCompare(
    right.display_name || right.agent_id,
  );
}
