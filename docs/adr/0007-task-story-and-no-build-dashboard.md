# ADR 0007: TaskStory And A No-Build Dashboard

## Decision

muxdev has one task-first Dashboard implemented as local embedded
HTML/CSS/JavaScript. It consumes a versioned TaskStory projection rather than
joining operational tables in the browser. The previous large alternative
Dashboard is removed; `render_live_dashboard_html` remains a compatibility
function for callers.

TaskStory remains a read model over existing facts. Its HMAC cursor, bounded
pages and output allowlist prevent clients from turning the Dashboard into an
unbounded diagnostic or secret-exfiltration interface.

## Consequences

- Installation has no Node or frontend-build dependency.
- Route, recovery, review and trust are visible on one task path.
- Rich SPA component ecosystems are unavailable by design.
- Replay is clearly separated from live state and production learning.
