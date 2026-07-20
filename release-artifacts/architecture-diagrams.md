# muxdev architecture diagrams

## Four layers

```mermaid
flowchart LR
  C["Composition: CLI / HTTP / MCP / RunEngine"] --> A["Adapters: Provider / SQLite / Config / Skills"]
  C --> P["Application: TaskService ports"]
  A --> D["Domain: Evidence / Run / Stage contracts"]
  P --> D
```

## Durable recovery

```mermaid
sequenceDiagram
  participant U as User
  participant E as RunEngine
  participant S as ControlStore
  participant P as ProviderAdapter
  U->>E: run or resume
  E->>S: persist job and stage=running
  E->>P: execute(StageExecutionInput)
  P-->>E: StageExecutionResult
  E->>S: persist output, usage, evidence, stage=completed
  alt human decision required
    E->>S: interaction=pending
    E-->>U: WAITING_HUMAN
    U->>S: approved/rejected
    U->>E: resume
  end
  E->>S: replay completed stages; reconcile opaque running stage
```

## Evidence and gate data flow

```mermaid
flowchart LR
  W["Workflow + Profile"] --> EP["Frozen EvidencePolicy"]
  R["Runtime observations"] --> ER["Typed EvidenceRecord events"]
  EP --> G["Deterministic Gate Engine"]
  ER --> G
  G --> GD["PASS / BLOCKED / WAITING_HUMAN"]
  G --> SC["Scorecard: completeness / reproducibility / integrity / independence"]
  GD --> REP["evidence-report.json"]
  SC --> REP
  REP --> DSSE["optional attestation.dsse.json"]
```
