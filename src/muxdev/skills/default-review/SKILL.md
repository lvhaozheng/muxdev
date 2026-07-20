---
name: default-review
description: Review plans, designs, changes, tests, or documentation for correctness and delivery risk. Use whenever an independent or skeptical review result is required.
---

# Review a frozen subject

- Review only the supplied subject and identify it with the provided digest.
- Lead with actionable findings ordered by realistic severity.
- Focus on correctness, regressions, unsafe behavior, missing verification, compatibility, and maintainability.
- Include file and line references when available and a concrete remediation for every finding.
- Treat absent information as missing context, not proof that a defect exists.
- State residual risk when no findings remain.

Return only the workflow's declared `ReviewResult`; the runtime establishes reviewer independence and the Gate Engine derives the outcome.
