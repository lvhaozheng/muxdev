---
name: default-secure
description: Review security and privacy risks. Use for explicit security reviews involving authentication, authorization, secrets, privacy, input handling, dependencies, logging, or abuse controls.
---

# Review security

- Identify assets, actors, trust boundaries, sensitive data, and realistic abuse paths.
- Classify findings as authentication, authorization, secrets, privacy, input, supply_chain, logging, or abuse.
- Rate severity from impact and exploitability, not keyword presence.
- Reference affected surfaces and give a concrete mitigation for every finding.
- Never request, expose, or reproduce secrets.

Return the workflow's declared `ReviewResult`. The runtime binds the review to the subject and the Gate Engine decides whether unresolved risk blocks delivery.
