# Trace2Skill

> Turn successful agent execution traces into reusable, validated Agent Skills.

Trace2Skill is a multi-agent learning system built on AgentTeams. It records how agents solve real tasks, extracts evidence-backed experience from multiple traces, generates a standard `SKILL.md` package, and validates that Skill on previously unseen tasks. When a candidate fails, the system preserves the failure evidence and uses it to produce a revised version.

The current v0.3 MVP focuses on npm and pnpm dependency failures in JavaScript/TypeScript CI pipelines.

**[Open the evidence replay demo →](https://stephenzsl2007-del.github.io/Trace2Skill/)**

## Why Trace2Skill?

Most agent systems retain a conversation or a final answer after a task. That is useful context, but it is not an executable, testable capability. Trace2Skill turns one-off execution experience into a software asset:

```text
Real task execution
→ Trace capture
→ Multi-trace Experience Model
→ Skill v1
→ Held-out validation
→ Evidence-driven refinement
→ Skill v2
→ Independent Worker consumption
```

An agent is never allowed to declare its own repair successful. A deterministic host Validator checks installation, build, tests, disappearance of the original error, and the allowed change boundary.

## Implemented MVP

- Three npm training fixtures: peer conflict, lockfile drift, and a missing dev dependency.
- Three held-out fixtures: a new npm peer conflict, pnpm frozen-lockfile failure, and a missing pnpm build dependency.
- AgentTeams contracts for Manager, Execution Agent, Trace Analyst, Skill Engineer, and Evaluator roles.
- An evidence-backed Experience Model generated from three real training traces.
- A complete `diagnose-ci-dependency-failure` Skill v1.
- A real v1 failure on the two unseen pnpm cases.
- Evidence-driven refinement that adds package-manager detection and npm/pnpm command branches.
- Skill v2 validation across all three held-out fixtures.
- Independent `consumer-worker` loading with package hash verification.
- FastAPI endpoints for full-loop execution, idempotency, state, events, traces, and Skills.
- Fail-closed orchestration: downstream phases stop while failure evidence remains available.

## AgentTeams' Role

AgentTeams provides orchestration and collaboration. The Manager assigns bounded phase tasks and passes artifact references; specialized Workers execute, analyze, generate, and evaluate. Trace2Skill connects those outputs into a learning loop, while the deterministic Validator remains the only success authority.

| Component | Responsibility |
|---|---|
| Manager | Phase dispatch, artifact hand-off, progress tracking |
| Execution Agent | Diagnose and repair an isolated fixture; produce a Trace |
| Trace Analyst | Extract an Experience Model from three training traces |
| Skill Engineer | Generate v1 and refine it from failure evidence |
| Evaluator | Arrange held-out trials and collect results |
| Validator (non-LLM) | Enforce install/build/test and safety gates |

See [Architecture](docs/architecture.md) for the component and data-flow boundaries.

## Quick Look

Open the evidence-backed offline showcase without starting a model or AgentTeams:

```powershell
Start-Process .\showcase\trace2skill-demo.html
```

The page is explicitly a replay of versioned evidence, not a live run.

## Run the Full MVP

### Requirements

- Python 3.12
- Docker Desktop
- AgentTeams v1.1.2
- Node.js with npm and pnpm available on `PATH`
- Ready `trace-worker`, `skill-worker`, and `consumer-worker` instances
- A Qwen-compatible model route through Higress

Tool locations can be overridden with `TRACE2SKILL_NODE`, `TRACE2SKILL_NPM`, and `TRACE2SKILL_PNPM`. Set `TRACE2SKILL_HOST_SHARE_ROOT` when the AgentTeams host-share root is not the current user's home directory.

### Install

```powershell
python -m pip install -e ".\product\backend[dev]"
```

### Ensure the Consumer Worker is Ready

```powershell
.\scripts\ensure-v03-consumer-worker.ps1
```

### Start the Trace-to-Skill Loop

```powershell
python .\scripts\run_v03_mvp.py --run-id demo-run
```

Inspect status and failure details:

```powershell
python .\scripts\run_v03_mvp.py --show demo-run
```

Each execution writes to an isolated `product/runs/<run-id>/` directory. A failed phase stops the pipeline and records a terminal error in `run.json`.

## Run the API

```powershell
python -m uvicorn trace2skill.api:app `
  --app-dir .\product\backend\src `
  --host 127.0.0.1 `
  --port 8000
```

Create a run with `POST /runs` and `kind: "full-loop"`. The API invokes the same orchestrator as the CLI; it does not return a separately mocked workflow.

## Generated Skill Package

```text
diagnose-ci-dependency-failure/
├── SKILL.md
├── references/
│   ├── decision-tree.md
│   └── failure-patterns.md
├── validators/
│   └── validate.py
├── evals/
│   └── cases.json
└── trace2skill.json
```

The package contains instructions, scope, preconditions, prohibited actions, validation rules, trace references, version metadata, and evaluation results. ZIP archives are reproducible build artifacts and are intentionally excluded from source control.

## Reproducible Evidence

One sanitized canonical acceptance run is kept in the repository so the core claim remains auditable without turning the Git repository into a data store:

- [Full-loop state](product/evidence/v0.3/full-loop/mvp-acceptance-02/run.json)
- [Experience Model](product/evidence/v0.3/full-loop/mvp-acceptance-02/experience/analysis.json)
- [Skill v1](product/evidence/v0.3/full-loop/mvp-acceptance-02/skills/diagnose-ci-dependency-failure/1.0.0-candidate.1/package/SKILL.md)
- [Skill v2](product/evidence/v0.3/full-loop/mvp-acceptance-02/skills/diagnose-ci-dependency-failure/2.0.0-candidate.1/package/SKILL.md)
- [v1 held-out results](product/evidence/v0.3/full-loop/mvp-acceptance-02/v1-probes/manifest.json)
- [v2 held-out results](product/evidence/v0.3/full-loop/mvp-acceptance-02/v2-probes/manifest.json)
- [API acceptance](product/evidence/v0.3/api-acceptance/mvp-api-acceptance-01.json)

These six controlled tasks demonstrate mechanism feasibility; they are not presented as a statistically significant general benchmark.

## Repository Layout

```text
product/backend/   FastAPI service and Trace2Skill core modules
product/agent-specs/ AgentTeams role contracts
product/fixtures/  Six deterministic npm/pnpm fixtures
product/evidence/  One canonical sanitized acceptance run
scripts/           Orchestration, validation, and environment helpers
skills/            Source Skill examples and validators
schemas/           Trace, Experience, evaluation, and fixture contracts
showcase/          Offline evidence replay
tests/             Regression tests for the earlier pilot interfaces
docs/              Architecture and design decisions
```

## Scope

The MVP supports JavaScript/TypeScript npm and pnpm CI dependency failures. It does not claim general software-engineering coverage.

AgentScope Studio tracing, a live Nacos upload/review/release/get lifecycle, a two-round qualification benchmark, and a Next.js product interface remain follow-up integrations. The existing code contains explicit adapter contracts for these boundaries, but the README does not represent them as completed production features.

## Security Model

- API keys, passwords, and tokens must never enter the repository, traces, Skills, or browser responses.
- Agents receive answer-free isolated repository copies.
- Validator-private configuration and reference repairs remain host-only.
- Package installation disables lifecycle scripts; build and test use allowlisted commands.
- Messages, tool arguments, and model output are sanitized before persistence.
- Bypass fixes such as `--force` and `--legacy-peer-deps` are prohibited.

## Contributing

Start with the [architecture overview](docs/architecture.md) and the [architecture decision records](docs/decisions/). Run the backend test suite before opening a change:

```powershell
python -m pytest product\backend\tests
```

Please keep generated archives, local run data, credentials, and benchmark output outside Git history.
