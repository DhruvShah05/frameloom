# Architecture Spec: "Loom" — Git for Dataframes

*(Loom is a placeholder codename used throughout this doc — swap in your real package name.)*

Your instinct on Polars/DuckDB + replay-on-open is correct, and it's actually the thing that makes the whole branching model cheap. This doc works out why, then goes deep on every subsystem: the versioning core, the execution engine, the API, the frontend, code generation, and each of the four features from before.

---

## 1. High-Level Architecture

```
 ┌─────────────────────────────────────────────────────────────┐
 │  CLI  (loom file.csv)                                        │
 │   - parses args, finds/creates project dir                   │
 │   - spawns local server, opens browser                       │
 └───────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  Local Backend (FastAPI, single process, localhost only)     │
 │                                                                │
 │   ┌──────────────┐   ┌──────────────────┐   ┌──────────────┐ │
 │   │ Project Store │   │ Operation Graph   │   │ Code Gen     │ │
 │   │ (SQLite/JSON) │◄──┤ Engine (the "git")│──►│ Module       │ │
 │   └──────────────┘   └────────┬──────────┘   └──────────────┘ │
 │                                │                                │
 │                                ▼                                │
 │                     ┌────────────────────┐                     │
 │                     │ Execution Engine    │                     │
 │                     │ Polars (primary) +  │                     │
 │                     │ DuckDB (join/audit) │                     │
 │                     └────────────────────┘                     │
 └───────────────────────────┬───────────────────────────────────┘
                              │  WebSocket + REST (JSON/Arrow)
                              ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  Frontend SPA (React, served by the same local process)       │
 │   - Grid view (virtualized, spreadsheet-like)                 │
 │   - Branch tree view (the git graph)                          │
 │   - Op inspector / transform menus                            │
 │   - Diff / audit split-screen                                 │
 │   - Recipe manager                                            │
 └─────────────────────────────────────────────────────────────┘
```

Everything runs in one local process. No cloud, no auth server for v1 — the "browser opens" experience is just a local server + `webbrowser.open()`, same trick Jupyter itself uses.

---

## 2. Core Insight: Operations Are the Source of Truth, Not Data

This is the most important architectural decision, so it goes first.

**Do not store dataframe snapshots per node.** Store the *operation* that produced each node. A node's data is always derivable by replaying the chain of operations from the root. This is exactly git's model: a commit isn't a full copy of your repo, it's a diff (well, a tree object, but conceptually a delta) — and `git checkout` "replays" history to materialize a working tree.

Why this matters for you specifically:

- **Branch switching becomes cheap** because you're not copying gigabytes of data per branch — you're just re-pointing which operation chain is "active" and re-evaluating it.
- **Polars' `LazyFrame` and DuckDB's relational views are both natively lazy** — building a 40-step chain costs almost nothing until you call `.collect()` (Polars) or actually `SELECT` from a view (DuckDB). The query optimizer also fuses/reorders steps (predicate pushdown, projection pushdown), so replay isn't literally "redo 40 passes over the data" — it's one optimized physical plan.
- **Code export falls out for free**, because the operation log is already the thing you turn into `.py` code — you're not reverse-engineering intent from a data diff.

The one thing you must add on top of "just replay everything" is **checkpointing**, because naive full replay does start to hurt once a chain gets long or involves expensive steps (joins, groupbys, window functions) on large data. More on this in §4.3.

---

## 3. Data Model

### 3.1 Operation

An `Operation` is a small, JSON-serializable, engine-agnostic description of one transform. This is the *only* thing that gets persisted long-term — everything else (Polars plans, DuckDB views) is rebuilt from these at runtime.

```python
@dataclass
class Operation:
    id: str                  # uuid
    type: str                 # "drop_column" | "filter_rows" | "fillna" |
                               # "rename" | "cast_dtype" | "groupby_agg" |
                               # "join" | "resample" | "rolling" | "sort" |
                               # "custom_expr" | ...
    params: dict              # JSON-safe args, e.g. {"columns": ["age"]}
    label: str                # human-readable, auto-generated but editable
    created_at: datetime
    engine_hint: str | None   # optional: "polars" | "duckdb" if op is engine-specific
```

Keep `type` as a **closed, curated set** you control (not "arbitrary pandas call"), except for one deliberate escape hatch (`custom_expr` — see §10, Security). A closed op vocabulary is what makes clean code generation and cross-engine execution possible.

### 3.2 Node (the "commit")

```python
@dataclass
class Node:
    id: str
    parent_id: str | None     # None only for the root (ingestion) node
    operation: Operation       # the op that transforms parent -> this node
    schema_hash: str            # cheap fingerprint (column names+dtypes), for UI
    row_count: int | None       # cached after first materialization, nullable
```

### 3.3 Branch (the "ref")

```python
@dataclass
class Branch:
    name: str
    head_node_id: str
    forked_from_node_id: str | None   # where it diverged from another branch
    created_at: datetime
```

`HEAD` is just "which branch (or detached node) is currently active" — identical to git.

### 3.4 Project (the whole session)

```python
@dataclass
class Project:
    id: str
    source_path: str            # original file.csv path
    source_hash: str            # so recipes can warn on a different file's schema
    nodes: dict[str, Node]      # the full graph, keyed by id
    branches: dict[str, Branch]
    head: str                   # branch name or node id (detached)
    recipes: list[RecipeMeta]
```

**Persistence:** the whole `Project` serializes to a single human-readable JSON (or a tiny SQLite file if you want transactional writes + easy querying of "all nodes on branch X"). It's small — kilobytes, not megabytes — because it's metadata, not data. Two nice side effects:

- Users can put the project file under **real git** and diff/merge/PR their data-cleaning history like code.
- Recipes (§7) are literally just a filtered slice of this same structure, so you don't need a second serialization format.

---

## 4. Execution Engine

### 4.1 Why Polars as primary

Polars' expression API maps almost 1:1 onto discrete UI actions (`.drop()`, `.filter()`, `.with_columns()`, `.group_by().agg()`), which makes both **building the lazy plan** and **generating readable code** straightforward — the code you generate can just be the Polars (or pandas, if you offer a target-language toggle) equivalent of the expression you already built internally. Polars' lazy engine also has a streaming/out-of-core mode, so "bigger than RAM" isn't a wall you hit immediately.

### 4.2 Why DuckDB alongside it, not instead of it

DuckDB earns its place for two specific things Polars is clunkier at:

- **The split-screen audit/reconciliation feature** — matching two tables on a key and classifying rows as match/mismatch/missing is a natural multi-way `JOIN` with `CASE WHEN` classification logic. Writing this as raw SQL (which DuckDB can run directly against Polars dataframes/Arrow data with zero-copy) is far more legible than hand-rolling it in a dataframe API.
- **Ad hoc, larger-than-memory joins** — DuckDB's out-of-core join implementation is very mature.

Recommendation: **Polars is the default execution engine for the main operation chain; DuckDB is invoked specifically for join-heavy and audit-mode operations**, via `duckdb.sql(...)` operating directly on the in-memory Polars/Arrow tables (no copy needed — both speak Arrow natively). The user never needs to know two engines are involved.

### 4.3 Replay strategy: lazy chains + checkpoints

Your proposed model — "save operations step by step, redo them when the tab opens" — is right, refined as follows:

1. Every `Node` maps to a `LazyFrame` recipe: `node.plan = parent.plan.<op>(...)`. Building this is nearly free (no computation happens).
2. Switching HEAD to another branch/node just sets "the active plan is `node.plan`" and triggers `.collect()` (or `.fetch(N)` for a fast preview of just the visible grid rows) for rendering.
3. **Checkpointing**: for nodes flagged as "expensive" (joins, groupbys, or simply every N-th node on a long chain), materialize and cache the result — in-memory (LRU, keyed by node id) and optionally spilled to a local Parquet file under the project's working directory. When replaying, walk *backward* from the target node until you hit a cached ancestor, then only replay forward from there, not from the true root.
4. Cache invalidation is simple because the graph is append-only: a node's cache is valid forever once computed (its inputs — parent + operation — never change; editing an existing step instead of appending a new one should be modeled as *creating a new node*, not mutating history, exactly like git).

This gives you git's "cheap branch switching" property without git's file-based storage model — the cache is a performance optimization, not part of the source of truth.

### 4.4 Preview vs. full materialization

The grid view never needs the full result — only the visible rows. Use `.fetch(n_rows)` / `LIMIT` for the live-editing preview, and only run a full `.collect()` when the user explicitly exports, saves a checkpoint, or asks for full stats. This keeps the "typing in the UI feels instant" experience even on a large file.

---

## 5. Backend API

FastAPI, REST for CRUD-ish operations, WebSocket for live grid updates (so recompute-in-progress can stream partial state / stay responsive on big data).

```
POST   /project/load                 { path } -> Project
GET    /project/{id}/tree            -> full node/branch graph, for the tree UI
POST   /project/{id}/op              { parent_node_id, operation } -> new Node
POST   /project/{id}/checkout        { target: branch_name | node_id } -> sets HEAD
GET    /project/{id}/data            { node_id, offset, limit } -> grid page (Arrow/JSON)
GET    /project/{id}/diff            { node_a, node_b } -> schema/row diff summary
POST   /project/{id}/branch          { name, from_node_id }
POST   /project/{id}/export/code     { node_id, target: "polars"|"pandas", mode: "script"|"colab_cell" }
POST   /project/{id}/recipe/save     { name, node_ids[] or branch }
POST   /project/{id}/recipe/apply    { recipe_name, new_source_path }
POST   /audit/compare                { path_a, path_b, key_columns } -> classified diff
```

Everything is addressed by **node id**, not "current state" — this is what lets the frontend show multiple branches simultaneously (for the side-by-side view) without the backend needing separate "sessions."

---

## 6. Frontend Architecture

- **Grid**: use a virtualized grid library (AG Grid Community, Glide Data Grid, or similar) — needed regardless of backend choice once you're dealing with >10k rows, since naive React tables die well before that.
- **Tree view**: a DAG-drawing library (or hand-rolled SVG, similar to how GitLens/GitKraken draw commit graphs) rendering nodes top-to-bottom or left-to-right, branches as colored lanes, current HEAD highlighted. Clicking a node = checkout, hovering = shows the op label/diff summary.
- **State management**: keep it simple — the tree graph and current node's data are the two pieces of server state; local UI state (which menu is open, form fields) stays in React state, not global store, until you have a concrete reason otherwise.
- **Op menus**: each op type maps to a small form (e.g. "Fill NA" → column picker + strategy dropdown). Submitting posts an `Operation` to `/project/{id}/op`.

---

## 7. Feature Deep Dives

### 7.1 Branching Tree

This *is* the core product — everything above was really building toward this feature specifically.

- **Fork**: "New Branch from here" on any node creates a `Branch` pointing at that node's id. No data is copied — see §3–4.
- **Compare branches side-by-side**: since both branches are just node ids, rendering "Branch A vs Branch B" is two independent calls to `/project/{id}/data` with different `node_id`s. The frontend just renders two grid panes.
- **Merge** is the one place git's model doesn't map cleanly onto tabular data (there's no line-based three-way merge for a dataframe). For v1, skip true merging — "merge" for you probably just means "take these specific columns/steps from Branch B and append them onto Branch A," which is really "cherry-pick an operation subsequence," not a merge. Model it as: user selects a range of nodes on Branch B, backend re-applies those operations' `Operation` objects (not the cached data) onto Branch A's HEAD, creating new nodes. This works because operations are engine-agnostic descriptions, so replaying them against a different parent is well-defined as long as required columns exist.
- **Export branch to `.py`**: walk from root to the branch's HEAD, feed the ordered `Operation` list into the code generator (§8).

### 7.2 Split-Screen Audit / Reconciliation

Distinct enough from the main editor that I'd build it as a semi-independent mode sharing only the grid component.

- User loads dataset A and B, picks shared key column(s).
- Backend runs (via DuckDB, zero-copy against the Polars tables):
  ```sql
  SELECT a.*, b.*,
         CASE
           WHEN b.key IS NULL THEN 'missing_in_b'
           WHEN a.key IS NULL THEN 'missing_in_a'
           WHEN a.* IS NOT DISTINCT FROM b.* THEN 'match'
           ELSE 'mismatch'
         END AS status
  FROM a FULL OUTER JOIN b ON a.key = b.key
  ```
  (real implementation compares column-by-column to know *which cells* mismatch, not just "row differs" — do this by generating one `CASE WHEN a.col IS DISTINCT FROM b.col` expression per shared column and returning a per-cell mismatch map alongside the row).
- Frontend colors rows/cells from the `status` + per-cell mismatch map.
- "Generate code to isolate discrepancies" is just: render the same SQL (or the DuckDB-Python equivalent) into a `.py` file — this feature's code export is naturally SQL-flavored, which is fine and arguably clearer than a dataframe-API equivalent for a join+classify operation.

### 7.3 Time Ops

This is "just" a curated set of `Operation` types with a nicer UI, not a new architectural component:

- `resample` op: params = `{time_col, freq, agg_map}` → Polars `group_by_dynamic` or `upsample`+`fill` depending on the exact behavior requested; DuckDB `time_bucket` as an alternative backend if it's cleaner for a given case.
- `rolling` op: params = `{window, agg, min_periods}` → Polars `.rolling()`.
- `fill_gaps` op (forward-fill weekends/holidays): params = `{time_col, calendar, method}`.
- The "Time Ops" menu is a UI affordance that only appears when a column's inferred dtype is datetime — no special backend needed beyond adding these op types to the vocabulary and their Polars/DuckDB + codegen implementations.

### 7.4 Recipes

A recipe is a **named, parameterized slice of the operation graph** — concretely, an ordered list of `Operation` objects (from root to a chosen node, or a chosen branch), plus a small manifest describing what it expects from the new input data.

```python
@dataclass
class Recipe:
    name: str
    operations: list[Operation]
    expected_schema: dict[str, str]   # column -> dtype, from when it was recorded
    source_hash_origin: str            # informational only
```

Applying a recipe to `new_data.csv --apply my_recipe`:

1. Load `new_data.csv`, infer its schema.
2. **Validate before replaying**: diff `expected_schema` vs. the new file's schema. Missing columns a required op touches, or dtype mismatches, get surfaced as warnings *before* execution — don't silently apply and hope. This is the failure mode you flagged as a risk, and it's important enough to be a first-class step, not an afterthought: show "this recipe expects column `region` (missing) and `signup_date` as datetime (found as string)" and let the user fix/skip/abort per step.
3. Replay each `Operation` in order against the new data, building a fresh node chain (this new run gets its own project/tree too, so the user can still branch off *this* run if they want to tweak something for this week's data specifically — recipes aren't a black box, they're just a fast-forward starting point).
4. Present the final grid for inspection before save — exactly as you described.

Recipes stored as their own small JSON files (or rows in a local recipes registry) so they're shareable independent of any one project — a teammate can drop a `.loom-recipe.json` file into their own `loom` invocation.

---

## 8. Code Generation Module

Decouple this completely from whichever engine executes the live preview. The generator's only input is the ordered list of `Operation` objects — never the Polars/DuckDB internal plan — because trying to "decompile" an optimized query plan back into readable code is a much harder and less reliable problem than rendering directly from your own structured op log, which you fully control.

```python
def render_op(op: Operation, target: Literal["pandas", "polars"]) -> str:
    template = OP_TEMPLATES[op.type][target]
    return template.format(**op.params)

def generate_script(operations: list[Operation], target: str) -> str:
    lines = [IMPORT_HEADER[target], f"df = {LOAD_STMT[target]}"]
    for op in operations:
        lines.append(render_op(op, target))
    return "\n".join(lines)
```

Each `(op.type, target)` pair has a hand-written, tested template — this is genuinely the highest-effort part of the whole project (matching Mito/bamboolib's years of edge-case polish), so budget for it accordingly and start with a small, very solid op vocabulary rather than a large, flaky one.

For **Colab cell injection**, the same `generate_script` output gets sent to the notebook frontend via Colab's JS bridge (`google.colab.kernel` messaging) to insert a new cell with that text, rather than written to a `.py` file — same generator, different delivery mechanism.

---

## 9. Project Persistence Layout

```
my_analysis.loom/
├── project.json          # the Node/Branch/Operation graph (source of truth)
├── source.csv.meta        # path + hash of the original file (not a copy, unless --snapshot passed)
├── cache/
│   ├── <node_id>.parquet  # materialized checkpoints, disposable
├── recipes/
│   └── my_recipe.json
```

`cache/` is fully disposable — deleting it just means the next replay is slower, not incorrect. Worth stating explicitly in the format so nobody's tempted to treat it as authoritative.

---

## 10. Security / Safety Considerations

- The server should bind to `localhost` only, never `0.0.0.0`, given it will often be handling sensitive spreadsheets (financial, health, PII-laden data per your own examples).
- If you offer a `custom_expr` escape-hatch op (letting users type raw Polars/pandas expressions for things the UI doesn't cover), that's arbitrary code execution by design — scope it clearly to the user's own machine/data (no different from running their own Jupyter cell), but don't let recipes silently re-execute someone else's `custom_expr` code without the user seeing what it does first, since recipes are exactly the kind of file people will casually share with teammates.

---

## 11. Suggested Build Order

1. **Core loop**: CLI → server → grid → a handful of ops (drop col, filter, rename, fillna, cast) → linear (non-branching) history → `.py` export. Get this feeling solid and fast before anything else.
2. **Branching tree**: add fork/checkout/side-by-side. This is the differentiating demo.
3. **Recipes**: save/apply/validate. This is the retention feature.
4. **Time Ops**: expand the op vocabulary once the core engine and codegen pattern are proven.
5. **Audit/diff mode**: build as a semi-separate module reusing the grid component.

---

## 12. Open Questions Worth Deciding Early

- **pandas or Polars (or both) as the exported code's target language?** Polars-native export is more honest to the execution engine; pandas export has the bigger addressable audience since most existing pipelines are pandas. A toggle is doable but doubles codegen template work — decide if you need both for v1 or can launch Polars-only.
- **How much of the op vocabulary is "generic transform" vs. domain-specific (e.g., your derivatives/finance example ops like MACD)?** Domain-specific op packs (finance, time-series, audit) could become a plugin system later, similar to how bamboolib let enterprises add custom transforms.
- **Merge semantics** (§7.1) — worth deciding explicitly rather than discovering the limitation mid-build.