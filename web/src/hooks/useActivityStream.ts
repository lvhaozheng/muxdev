import { useEffect, useRef } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { ActivityEvent, ConversationSnapshot } from "../types";

const stateEvents = new Set([
  "assignment.started",
  "assignment.reported",
  "interaction.created",
  "interaction.responded",
  "workspace.reconciled",
  "delivery.candidate_ready",
  "delivery.accepted",
  "delivery.answered",
  "workspace.rolled_back",
  "review.changes_requested",
]);

export function useActivityStream(
  projectId: string | null,
  conversationId: string | null,
  queryClient: QueryClient,
): "live" | "reconnecting" | "offline" {
  const state = useRef<"live" | "reconnecting" | "offline">("offline");
  const refreshTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!projectId || !conversationId) {
      state.current = "offline";
      return;
    }
    const snapshot = queryClient.getQueryData<ConversationSnapshot>([
      "projects",
      projectId,
      "conversation",
      conversationId,
    ]);
    const after = snapshot?.last_sequence ?? 0;
    const source = new EventSource(
      `/api/v2/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}/stream?after=${after}`,
      { withCredentials: true },
    );
    state.current = "reconnecting";
    source.onopen = () => {
      state.current = "live";
    };
    source.onerror = () => {
      state.current = "reconnecting";
    };
    source.addEventListener("activity", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as ActivityEvent;
      queryClient.setQueryData<ConversationSnapshot>(
        ["projects", projectId, "conversation", conversationId],
        (current) => {
        if (!current || current.timeline.some((item) => item.event_id === event.event_id)) {
          return current;
        }
        return {
          ...current,
          timeline: [...current.timeline, event].slice(-200),
          last_sequence: Math.max(current.last_sequence, event.sequence),
        };
        },
      );
      if (stateEvents.has(event.type)) {
        if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
        refreshTimer.current = window.setTimeout(() => {
          void queryClient.invalidateQueries({
            queryKey: ["projects", projectId, "conversation", conversationId],
          });
          void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "changes", conversationId] });
          void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "review", conversationId] });
          void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "conversations"] });
        }, 350);
      }
    });
    return () => {
      source.close();
      if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
      state.current = "offline";
    };
  }, [conversationId, projectId, queryClient]);

  return state.current;
}
