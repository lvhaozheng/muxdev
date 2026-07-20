# muxdev verification report

Measured on Windows with Python 3.13 after the compact refactor.

| Layer | Result | Time |
|---|---:|---:|
| Quick product verification | passed | 12.03s |
| Unit + contract | 14 passed | 2.63s |
| Integration | 7 passed | 66.26s |
| Migration | 2 passed | 0.04s |
| Release | 2 passed | 7.72s |
| Ruff | passed | 2.2s |
| Compileall | passed | 2.2s |
| Documentation links/layout | passed | 2.2s |

The quick suite is below the 45-second target. Integration is executed separately and reports per-test duration:

| Integration case | Time |
|---|---:|
| strict pause → policy mutation → approve → resume | 16.05s |
| lite PASS plus artifact tamper detection | 15.60s |
| standard Profile independent-review block | 12.57s |
| forged TestResult versus Runtime exit code | 11.33s |
| event-chain tamper detection | 9.97s |
| HTTP/MCP read surfaces | 0.02s |
| fixed interface budgets | 0.01s |

Coverage includes normal PASS, high-score BLOCKED, missing/pending/rejected interaction behavior, self-review, wrong review subject, Provider-controlled gate fields, forged test success, runtime exit-code contradiction, artifact tampering, event-chain verification, Skill permission escalation, legacy Skill gate migration warnings, DSSE signing/verification, v7 migration, rollback, and idempotent restart.

The host Conda environment emits a `requests` dependency compatibility warning. muxdev does not depend on or import `requests`; Ruff, compilation, and all test layers still pass.
