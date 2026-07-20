# DUCK-E — Plan

This file follows the workspace's standard `docs/plan/plan.md` convention. It does
not replace `docs/plan.md` (the pre-existing "DUCK-E Modernization Plan," written
during the AG2 → custom-RealtimeSession rewrite) — that document remains the
detailed record of the Change 1–9 rewrite phases and is linked here rather than
duplicated. This file is the home for forward-looking architecture decisions
(ADRs) going forward, starting with the first one below.

## What DUCK-E ships

A voice assistant web app (FastAPI backend + vanilla JS/WebRTC frontend) that
talks to the OpenAI Realtime API, with tool-calling for weather, web search,
web fetch, per-user persistent memory, and mid-session voice switching. See
`README.md` for the full feature list and `docs/plan.md` for the rewrite
history.

Deployed live at `https://ducke.ardenone.com` (ardenone-cluster, namespace
`ducke`, Google-OAuth-gated except the `/session` WebSocket path) via
`declarative-config/k8s/ardenone-cluster/ducke/`. Image built by the
`duck-e-build` Argo Workflow in iad-ci, published to
`ronaldraygun/duck-e:<version>`.

## Improvement review — 2026-07-20

A fleet-wide "improve what's shipped" pass (separate from the 2026-07-19
plan-vs-code gap audit) inspected the live deployment, not just the repo. Two
concrete findings drove this review:

1. The running pod (`kubectl get pods -n ducke`, ardenone-cluster) has been up
   36 days on image `ronaldraygun/duck-e:0.2.106`. `declarative-config`'s last
   commit touching `ducke-deployment.yml` was 2026-06-03. The repo's `VERSION`
   is now `0.2.124+` — 18+ versions, including the ag2client→ducke rebrand,
   have shipped an image but never reached the live deployment because nothing
   updates the k8s manifest automatically. Filed as a bead (see below).

2. `app/memory.py` writes per-user fact files to `/data/memory/`, and
   `app/middleware/cost_protection.py` keeps session/hourly spend counters
   in-process (with optional Redis backing that isn't wired up). Neither
   `ducke-deployment.yml` nor any other file in
   `declarative-config/k8s/ardenone-cluster/ducke/` defines a volume or
   `REDIS_URL`. Both stores live only in the pod's writable container layer /
   process memory. This is the subject of ADR-1 below.

The full list of ideas considered, and which became beads vs. the ADR, is in
the review notes referenced from the beads themselves (label
`artifact-improvement` in this repo's `.beads/`).

---

## ADR-1: 2026-07-20 — Durable state backend for user memory and cost-protection counters

### Context

DUCK-E advertises "Persistent memory" as a headline feature (README: *"DUCK-E
stores per-user facts with categories, confidence scores, and time decay, and
surfaces them at the start of each session"*) and ships a cost-protection
system with per-session, hourly, and circuit-breaker spend caps
(`app/middleware/cost_protection.py`) meant to hard-stop runaway OpenAI
Realtime API spend.

Both subsystems are tested at the application level — `notes/bf-44yc2.md` and
`notes/bf-6i5-verification.md` record passing tests for "cross-session memory
persistence" — but those tests exercise the file-write/read path within a
single running process. They do not exercise pod-restart durability, because
there is no PVC to exercise:

- `app/memory.py` persists facts as one JSON file per user under
  `/data/memory/{sha256(email)}.json` (`DEFAULT_MEMORY_DIR = "/data/memory"`).
- `declarative-config/k8s/ardenone-cluster/ducke/ducke-deployment.yml` defines
  no `volumes:` / `volumeMounts:` at all. `/data/memory` is therefore part of
  the container's ephemeral writable layer.
- `app/middleware/cost_protection.py` keeps `session_costs`,
  `session_start_times`, and hourly/circuit-breaker counters in a plain
  in-process `Dict`. It has an optional Redis-backed code path
  (`redis_url` / `REDIS_URL`), but no `REDIS_URL` is set anywhere in
  `ducke-secret`/`ducke-config` for the live deployment.
- The Deployment uses `RollingUpdate` with `maxUnavailable: 0` — every image
  bump creates a fresh pod. Combined with finding #1 above (deploys have been
  rare, ~monthly), this means: on the occasions the app *does* get redeployed,
  or restarts after a crash/OOM/node drain, every stored user fact and every
  in-flight spend counter — including the circuit-breaker "disable new
  sessions for 30 minutes" state meant to prevent cost overruns — silently
  resets to empty. Nothing alerts on this; it just looks like a fresh start.

This is a correctness gap in a feature the README markets as a differentiator,
and a safety gap in the one mechanism meant to cap real OpenAI billing
exposure.

The cluster already runs infrastructure that fits each need:
- `k8s/ardenone-cluster/valkey/` — a shared Valkey (Redis-compatible)
  instance, already consumed by `immich`, `options`, `botburrow`, and
  `mission-control`. It has no PVC/AOF persistence itself (cache-oriented),
  which is fine for cost-protection counters (bounded by an hourly/30-minute
  window) but wrong for long-lived user facts.
- Longhorn (`storageclass longhorn`/`longhorn-ha`) and NFS
  (`storageclass nfs-synology`) are both available for a small PVC. Per this
  workspace's current operational notes, 12/51 Longhorn volumes on this
  cluster are presently faulted (single-replica risk); `nfs-synology` is the
  lower-risk choice for a small, low-throughput, append-mostly JSON store.

### Decision

Adopt a two-part durable-state design, matched to each subsystem's actual
durability requirement, reusing existing cluster infrastructure instead of
introducing new stateful components:

1. **Cost-protection counters → shared Valkey.** Set `REDIS_URL` in
   `ducke-secret` to point at the existing `valkey.valkey.svc.cluster.local`
   instance (already-supported code path in `cost_protection.py`, so this is
   config-only, no app code change). Session/hourly/circuit-breaker state
   survives individual duck-e pod restarts; correctness no longer depends on
   the pod never restarting mid-window.

2. **User memory facts → a dedicated PVC.** Add a small
   (1Gi, `storageClassName: nfs-synology`) `PersistentVolumeClaim`, mount it
   at `/data/memory` in `ducke-deployment.yml`. No app code change — `memory.py`
   already writes/reads that exact path. `ReadWriteOnce` is sufficient since
   `replicas: 1`.

Both changes are manifest-only (`declarative-config`) plus one env var; no
duck-e application code changes are required for step 1, and step 2 requires
only the PVC + volumeMount addition. Implementation is tracked as a bead
against `declarative-config`'s `k8s/ardenone-cluster/ducke/` path (GitOps —
commit + ArgoCD sync, never a live `kubectl` mutation), referenced from this
ADR.

### Alternatives Considered

- **Do nothing / accept ephemeral state.** Rejected — the README's
  "persistent memory" claim would remain false in practice, and the
  cost-protection circuit breaker's core job (stop runaway spend) is
  undermined by a mechanism that resets on the exact kind of event
  (pod churn) it's supposed to be resilient through.
- **Move everything (memory + cost state) into Valkey.** Simpler
  operationally (one dependency, already deployed), but Valkey here runs
  without a PVC or AOF/RDB persistence — it's explicitly a cache tier shared
  by several apps. Putting the only copy of long-term user memory in an
  unpersisted, multi-tenant cache is a worse durability story than a
  dedicated PVC, and conflates DUCK-E's user data with unrelated apps'
  cache traffic.
- **Move everything to a PVC (SQLite or JSON files, including cost
  counters).** Avoids adding a Redis dependency, but cost-protection state is
  high-frequency, short-lived, and already has a working Redis code path —
  building/testing a PVC-based equivalent would be strictly more work for a
  subsystem Valkey already fits well. Also loses the built-in TTL/expire
  semantics `cost_protection.py` already uses for hourly windows
  (`redis_client.expire(...)`).
- **External managed DB (e.g., Postgres via CNPG, already present in-cluster
  as `cnpg`/`cnpg-system`).** Overkill for a per-user JSON-fact store this
  small; adds a second stateful dependency and migration surface for no
  benefit over a PVC at DUCK-E's current scale.
- **Object storage (B2/S3, per the ARMOR/native-ads pattern used elsewhere in
  this workspace).** Viable long-term if memory data outgrows a single PVC or
  needs cross-cluster durability, but is more moving parts than warranted
  today; revisit if DUCK-E ever needs multi-replica or multi-cluster
  deployment.

### Consequences

- User-facing: memory and voice history genuinely survive redeploys and
  crashes, matching what the README already claims.
- Safety: the cost circuit-breaker's guarantee ("no new sessions for 30
  minutes after $100 in an hour") holds even across a pod restart mid-window,
  closing a real (if narrow) cost-overrun gap.
- Ops: two new stateful dependencies for this Deployment (a PVC, and a shared
  Valkey connection) where there were previously zero. The PVC needs a place
  in future DR/backup planning (currently no backup story for `/data/memory`
  content — worth a separate bead once the PVC exists).
- No app code changes required to realize either half of this decision;
  everything is `declarative-config` manifest + one secret value, keeping the
  change entirely within this workspace's GitOps flow.
