# GSD Pi — Jarvis Development Environment Bootstrap

You are operating at the root of the Jarvis repository.

Your task in this phase is **NOT to implement, redesign, refactor, or rebuild Jarvis yet**.

Your only objective is to initialize and validate the complete engineering environment that will be used to develop Jarvis.

The environment must be deterministic, reusable, minimal, multi-agent capable, token-efficient, and safe to use for the subsequent full Jarvis implementation.

Do not begin product implementation until this bootstrap is completely validated.

---

# 1. TARGET ENGINEERING STACK

Jarvis development must use the following architecture:

```text
GSD Pi
│
├── Project context / planning / execution
│
├── Native multi-agent orchestration
│   ├── planner
│   ├── architect
│   ├── implementation agents
│   ├── reviewer
│   └── verification agents
│
├── Context optimization
│   ├── Ponytail
│   └── Context Mode
│
├── Engineering knowledge / skills
│   ├── Matt Pocock / high-quality TypeScript practices where applicable
│   ├── architecture
│   ├── testing
│   ├── security
│   ├── performance
│   └── Windows/Python engineering
│
├── UX/UI skills
│   └── only when a visual interface is involved
│
└── Model routing
    ├── fast/default model
    ├── strong reasoning model
    └── DeepSeek fallback
```

GSD Pi is the **single orchestration layer**.

Do not create a second agent framework around GSD.

---

# 2. TOOLS AND FRAMEWORKS THAT MUST NOT BE INTRODUCED

Do not install, initialize, restore, or depend on:

* Superpowers
* ECC
* Everything Claude Code
* Spec Kit
* OpenSpec
* Graphify
* DeepSeek Harness
* custom agent orchestration frameworks
* duplicate spec/planning systems
* another GSD implementation
* legacy `.agentic` systems
* unnecessary MCP servers
* unnecessary skills copied blindly from public repositories

If remnants of these exist, identify them.

Do not delete anything until you have established whether it is currently required and whether removal is safe.

The desired architecture is deliberately small:

```text
GSD Pi
+ Ponytail
+ Context Mode
+ selected engineering skills
+ selected modern UX/UI skills
```

Nothing more unless there is a demonstrated technical requirement.

---

# 3. VERIFY GSD PI FIRST

Determine the actual current environment instead of assuming anything.

Verify:

```bash
gsd --version
```

Inspect:

* installed GSD Pi version
* repository integration
* project initialization state
* `.planning/`
* `.gsd/`
* GSD configuration
* provider configuration
* agent configuration
* skill configuration
* current Git state
* ignored files relevant to GSD
* existing project instructions
* existing AI/agent configuration files

Do not overwrite valid project state.

If GSD has already been partially initialized, normalize it instead of creating conflicting structures.

Use the **native GSD Pi architecture and conventions for the installed version**.

Do not invent configuration files that GSD Pi does not support.

---

# 4. INITIALIZE THE PROJECT FOR GSD

Jarvis must become a properly initialized GSD project.

Establish project-level context for:

```text
Jarvis
```

The bootstrap must leave GSD able to understand:

* repository structure
* existing implementation
* languages
* dependencies
* runtime
* tests
* configuration
* development commands
* build commands
* execution commands
* operating-system constraints
* important architectural boundaries

Jarvis is a **Windows-first assistant runtime**.

The subsequent implementation may involve Python, external APIs, audio processing, subprocesses and Hermes, but this phase must not prematurely decide implementation details beyond what already exists in the repository.

The repository itself is the source of truth.

---

# 5. PONYTAIL

Verify whether Ponytail is already available and correctly integrated.

If not present, install/configure it using its current supported integration mechanism.

Ponytail exists to reduce unnecessary context/token consumption.

It must NOT become:

* another orchestrator
* another planner
* another agent hierarchy

Its responsibility is context efficiency.

Configure it so GSD agents avoid repeatedly loading:

* entire repositories
* large generated files
* dependency directories
* irrelevant documentation
* old logs
* build output
* binary assets

Prefer targeted context retrieval.

Validate that Ponytail actually participates in the GSD workflow.

Do not claim successful integration based only on files existing.

---

# 6. CONTEXT MODE

Verify and configure Context Mode.

Its responsibility is to provide agents with the smallest useful context for the current task.

The expected behavior is:

```text
repository
    ↓
task
    ↓
relevant architectural area
    ↓
relevant modules/files
    ↓
minimal context
    ↓
agent
```

NOT:

```text
repository
    ↓
dump everything into the model
```

Context Mode and Ponytail must complement each other.

They must not duplicate planning or agent orchestration.

Validate their integration.

---

# 7. SKILLS POLICY

Do NOT create a huge skill collection.

We want a curated engineering skill layer.

Skills should provide **specialized knowledge**, while GSD controls the workflow.

The base categories should cover:

```text
architecture
python
typescript when applicable
testing
TDD where appropriate
security
performance
concurrency
async programming
Windows integration
APIs
subprocess/process management
observability
error handling
resilience
audio/streaming where needed
UX/UI when a UI is involved
```

Prefer established, maintained, high-quality skill sources.

For TypeScript-related work, incorporate relevant practices associated with strong modern TypeScript engineering such as those popularized by Matt Pocock where applicable.

Do NOT install dozens of overlapping skills.

For every skill considered, ask:

```text
Does this skill provide knowledge that GSD itself does not already provide?
```

If the answer is no, do not add it.

---

# 8. UX/UI SKILLS

Jarvis may eventually have visual surfaces.

Prepare a small set of high-quality modern UX/UI skills, but do not load them for backend/runtime work.

They should cover concepts such as:

* interaction design
* modern product UI
* accessibility
* responsive layout
* visual hierarchy
* motion/animation
* design systems
* desktop application UX

Prefer maintained, respected current sources.

Avoid generic collections containing hundreds of prompts.

UX/UI skills must be invoked only when relevant.

---

# 9. MULTI-AGENT MODEL

Use **GSD Pi native agents/subagents**.

Do not create another orchestration system.

The conceptual workflow should support:

```text
MAIN GSD SESSION
       │
       ├── exploration
       ├── architecture
       ├── implementation
       ├── independent review
       └── verification
```

Parallelization should happen only when tasks are genuinely independent.

Examples:

```text
Agent A → repository/runtime analysis
Agent B → dependency/API investigation
Agent C → tests/verification
```

Then:

```text
results
   ↓
main GSD context
   ↓
decision
```

Do not parallelize multiple agents modifying the same files without a clear isolation strategy.

---

# 10. MODEL ROUTING

Configure the workflow around **task complexity**, not around one model for everything.

The expected strategy is conceptually:

```text
CHEAP / FAST
    ↓
simple exploration
file discovery
small deterministic changes
routine checks
basic implementation

STRONG REASONING
    ↓
architecture
cross-module reasoning
difficult debugging
design decisions
critical reviews

DEEPSEEK
    ↓
cost-effective execution
fallback capacity
provider quota fallback
suitable implementation/reasoning tasks
```

Use the providers/models actually available in the current GSD Pi environment.

Do not invent provider identifiers or configuration syntax.

Discover the supported provider configuration first.

---

# 11. PROVIDER FALLBACK

This is mandatory.

We have already encountered provider quota exhaustion during multi-agent execution.

A model/provider quota failure must NOT unnecessarily stop the workflow if another configured provider can perform the task.

Implement or configure the supported equivalent of:

```text
preferred model
      ↓
request
      ↓
success ─────────────→ continue
      │
      └─ quota/rate/provider unavailable
                    ↓
                 DeepSeek
                    ↓
                  retry
                    ↓
                 continue
```

Fallback is appropriate for errors such as:

* weekly quota exhausted
* rate limit
* provider temporarily unavailable
* model temporarily unavailable

Fallback must NOT hide:

* malformed prompts
* programming errors
* invalid configuration
* authentication errors that require user action
* failed tests
* incorrect code

Record which provider/model actually executed a task when practical.

If GSD Pi already exposes native fallback/routing capabilities, use them.

Do not build a redundant custom router unless native capabilities cannot satisfy the requirement.

---

# 12. CREDENTIALS

Never place secrets in:

* source code
* Git
* `.planning`
* prompts
* documentation
* agent instructions

Use the provider authentication mechanisms supported by GSD Pi.

Check which providers are currently authenticated.

At minimum, identify availability of:

* OpenAI/Codex
* Anthropic/Claude
* DeepSeek

Do not print credentials.

If authentication is missing, clearly mark only that capability as blocked.

Do not mark the entire environment failed if another provider works.

---

# 13. DEVELOPMENT WORKFLOW

After initialization, future Jarvis work should follow:

```text
REQUEST
   ↓
GSD understanding / requirements
   ↓
targeted repository exploration
   ↓
architecture / plan
   ↓
tasks
   ↓
implementation
   ↓
tests
   ↓
independent review
   ↓
runtime verification
   ↓
acceptance evidence
```

For substantial changes:

```text
PLAN
 → IMPLEMENT
 → TEST
 → REVIEW
 → FIX
 → VERIFY
```

No agent may declare a feature complete purely because code was generated.

---

# 14. TESTING POLICY

Testing is mandatory but pragmatic.

Use:

* unit tests
* integration tests
* contract tests
* end-to-end tests
* runtime smoke tests

according to the change.

Use TDD when it improves confidence or design.

Do not enforce ceremonial TDD where it provides no practical value.

For bug fixes, prefer:

```text
reproduce
→ failing regression test when practical
→ fix
→ passing test
```

---

# 15. REVIEW POLICY

Substantial implementation work must receive an independent review pass.

The reviewer should examine:

* correctness
* architecture
* unnecessary complexity
* regressions
* race conditions
* cancellation behavior
* resource leaks
* security
* performance
* error handling
* test quality
* hidden coupling

The implementing agent's assertion that the work is correct is not sufficient evidence.

---

# 16. VERIFICATION POLICY

Every GSD milestone must end with evidence.

Possible evidence includes:

```text
tests passing
build passing
lint passing
type checking passing
runtime smoke test passing
expected command output
integration test passing
manual acceptance scenario validated
```

Never report:

```text
DONE
COMPLETE
PRODUCTION READY
```

without evidence.

Use more precise states:

```text
VERIFIED
PARTIALLY VERIFIED
BLOCKED
FAILED
NOT TESTED
```

---

# 17. CONTEXT DISCIPLINE

Agents must not repeatedly read the entire repository.

Use:

```text
search
→ identify subsystem
→ inspect relevant files
→ expand only if necessary
```

Use Ponytail and Context Mode for this.

Avoid feeding:

* `node_modules`
* virtual environments
* build artifacts
* large lockfiles unless relevant
* binaries
* generated files
* huge logs
* unrelated documentation

into model context.

---

# 18. GIT SAFETY

Before modifying repository configuration:

```bash
git status
```

Understand current changes.

Do not destroy unrelated user work.

Never:

* force reset user changes
* delete untracked files blindly
* rewrite history unnecessarily
* push without explicit instruction
* commit secrets

For bootstrap-generated configuration changes, keep the diff focused.

---

# 19. LEGACY CLEANUP

Inspect for obsolete agent infrastructure.

Examples:

```text
.agentic/
.superpowers/
.claude/
old GSD implementations
old spec frameworks
unused agent prompts
duplicated skills
obsolete MCP configuration
Graphify integration
DeepSeek Harness
ECC remnants
```

Classify each as:

```text
KEEP
MIGRATE
REMOVE
UNKNOWN
```

Do not delete `UNKNOWN`.

Only remove obsolete infrastructure after confirming that the desired GSD Pi setup replaces it.

---

# 20. PROJECT INSTRUCTIONS

Create or normalize the repository-level instructions necessary for agents to understand how engineering work must be performed.

Keep them concise.

The repository instructions should explain:

* GSD Pi is the orchestrator
* Ponytail handles context efficiency
* Context Mode handles contextual selection
* skills provide specialized knowledge
* native GSD multi-agent is used
* independent verification is required
* provider fallback should be used
* repository state is authoritative
* secrets must never be committed

Do not create hundreds of lines of repeated instructions if GSD already carries those semantics.

---

# 21. INITIAL REPOSITORY BASELINE

Once the environment is configured, perform a lightweight repository baseline.

Determine:

```text
repository structure
language(s)
dependency management
existing tests
existing runtime entrypoints
existing architecture
configuration
technical debt visible at a high level
Git status
```

Do NOT solve those issues yet.

The baseline exists so the subsequent Jarvis implementation prompt starts from an accurate repository state.

---

# 22. REQUIRED VALIDATION

Do not finish after configuration.

Prove that the environment works.

At minimum validate:

### A. GSD

```text
GSD starts correctly
project recognized
project context available
planning/runtime state valid
```

### B. Provider

Execute a minimal provider-backed task.

### C. Native agent

Execute at least one harmless subagent task.

Example:

```text
Inspect one small repository area and return a short factual summary.
```

### D. Parallel agents

If supported, run two independent harmless analysis agents concurrently.

Confirm that they actually execute concurrently through GSD native orchestration.

### E. Ponytail

Show that context selection/token optimization is active.

### F. Context Mode

Show that a targeted task receives targeted context rather than an indiscriminate repository dump.

### G. Fallback

Perform a safe validation of the configured model fallback mechanism where the installed GSD capabilities allow it.

If intentionally forcing a provider failure is unsafe or unsupported, validate the configuration path and state that runtime fallback still requires natural failure evidence.

Do not fabricate evidence.

### H. Git

Verify that bootstrap changes are deliberate and repository state is understood.

---

# 23. REQUIRED FINAL REPORT

When bootstrap is complete, output exactly these sections:

## 1. Environment

Include:

```text
GSD Pi version
project initialization status
Git status
operating environment
```

## 2. Providers

Table:

| Provider | Authenticated | Primary/Fallback | Validation |
| -------- | ------------- | ---------------- | ---------- |

## 3. Context Stack

Table:

| Component    | Installed | Integrated | Verified |
| ------------ | --------- | ---------- | -------- |
| Ponytail     |           |            |          |
| Context Mode |           |            |          |

## 4. Skills

List ONLY the skills retained and explain briefly why each one exists.

## 5. Multi-Agent Validation

Report:

```text
single subagent:
parallel subagents:
review capability:
verification capability:
```

Include actual evidence.

## 6. Model Routing

Show the final routing/fallback strategy actually supported by the installed environment.

## 7. Legacy Cleanup

Table:

| Component | Decision | Reason |
| --------- | -------- | ------ |

## 8. Repository Baseline

Very concise description of the existing Jarvis repository.

Do not redesign Jarvis yet.

## 9. Validation

Use:

```text
PASS
PARTIAL
BLOCKED
FAIL
```

for every validation performed.

## 10. Final Readiness

The final line must be exactly one of:

```text
GSD ENVIRONMENT READY FOR JARVIS IMPLEMENTATION
```

or

```text
GSD ENVIRONMENT NOT READY
```

If it is not ready, state the exact blockers immediately before that line.

---

# 24. STOP CONDITION

This phase ends when the engineering environment is working.

DO NOT:

* rebuild Jarvis
* implement the voice runtime
* integrate Hermes into Jarvis
* change STT/TTS
* implement memory
* optimize response latency
* implement the startup sequence
* redesign the application

Those belong to the **next GSD milestone**.

The purpose of this phase is exclusively to ensure that when the Jarvis implementation begins, the engineering machinery is already correct.

Begin by inspecting the current repository and GSD Pi installation.

Do not assume configuration.

Do not ask me to choose tools already defined above.

Proceed autonomously, validate each layer, fix bootstrap problems that can safely be fixed, and stop only when the development environment itself has been verified.
