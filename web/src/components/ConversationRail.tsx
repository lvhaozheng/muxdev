import {
  ChatCircleDots,
  ClockCounterClockwise,
  MagnifyingGlass,
  Plus,
  WarningCircle,
} from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { labelStatus } from "../labels";
import type { Conversation } from "../types";

interface Props {
  conversations: Conversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
}

const groupOrder = ["needs_you", "active", "ready", "history"] as const;
const groupLabels = {
  needs_you: "Needs You",
  active: "Active",
  ready: "Ready",
  history: "History",
};

function groupFor(status: string): (typeof groupOrder)[number] {
  if (status === "needs_user") return "needs_you";
  if (["working", "verifying", "recovering", "clarifying"].includes(status)) return "active";
  if (["candidate_ready", "awaiting_acceptance"].includes(status)) return "ready";
  return "history";
}

export function ConversationRail({
  conversations,
  selectedId,
  onSelect,
  onCreate,
}: Props) {
  const [search, setSearch] = useState("");
  const groups = useMemo(() => {
    const result: Record<(typeof groupOrder)[number], Conversation[]> = {
      needs_you: [],
      active: [],
      ready: [],
      history: [],
    };
    const query = search.trim().toLocaleLowerCase();
    for (const conversation of conversations) {
      if (
        query &&
        !`${conversation.title} ${conversation.goal}`.toLocaleLowerCase().includes(query)
      ) {
        continue;
      }
      result[groupFor(conversation.status)].push(conversation);
    }
    return result;
  }, [conversations, search]);

  return (
    <aside className="conversation-rail" aria-label="Conversation 列表">
      <div className="brand-row">
        <span className="brand-mark" aria-hidden="true">
          <ChatCircleDots weight="fill" />
        </span>
        <div>
          <h1>MuxDev 工作台</h1>
          <span>Trusted delivery conversations</span>
        </div>
      </div>
      <button className="primary rail-create" type="button" onClick={onCreate}>
        <Plus weight="bold" />
        新建任务
      </button>
      <label className="search-box">
        <MagnifyingGlass aria-hidden="true" />
        <span className="sr-only">搜索 Conversation</span>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索 Conversation"
        />
      </label>
      <nav className="conversation-groups">
        {groupOrder.map((group) =>
          groups[group].length ? (
            <section className="conversation-group" key={group}>
              <header>
                {group === "needs_you" ? (
                  <WarningCircle aria-hidden="true" />
                ) : group === "history" ? (
                  <ClockCounterClockwise aria-hidden="true" />
                ) : (
                  <ChatCircleDots aria-hidden="true" />
                )}
                <span>{groupLabels[group]}</span>
                <small>{groups[group].length}</small>
              </header>
              {groups[group].map((conversation) => (
                <button
                  type="button"
                  className={`conversation-item ${
                    selectedId === conversation.conversation_id ? "selected" : ""
                  }`}
                  key={conversation.conversation_id}
                  onClick={() => onSelect(conversation.conversation_id)}
                  aria-current={
                    selectedId === conversation.conversation_id ? "page" : undefined
                  }
                  aria-label={conversation.title}
                >
                  <span className={`attention-dot ${group}`} aria-hidden="true" />
                  <span className="conversation-copy">
                    <strong>{conversation.title}</strong>
                    <small>{labelStatus(conversation.status)}</small>
                  </span>
                </button>
              ))}
            </section>
          ) : null,
        )}
      </nav>
    </aside>
  );
}
