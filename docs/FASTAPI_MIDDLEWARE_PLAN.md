# Markdownizer FastAPI Middleware — Implementation Plan

**Status:** Draft · **Date:** 2026-09-30 · **Repo:** `markdownizer-fastapi-middleware`
**Upstream:** [`markdownizer`](https://github.com/mohammadkhoddami/Markdownizer) 0.4.5 (IR v1)
**Prepared in response to maintainer feedback:** write the plan as one Markdown file first.

> **Implementation status:** milestones M0–M4 (package, middleware, route
> enrichment, security, docs, tests) are implemented in this repository under
> `markdownizer_fastapi/` and `tests_fastapi/`. The features below describe
> the shipped behavior; the "Phases" section records how it was built.

---

## 1. Summary

Ship `markdownizer-fastapi`: a small, deterministic FastAPI/Starlette
integration that exposes the same Codebase-to-Context artifacts the
`markdownizer` CLI produces — IR, budgeted context, stats, `llms.txt` — over
HTTP, and enriches them with **live FastAPI route metadata** that static AST
analysis alone cannot see (router prefixes, dependencies, response models).

One sentence:

> Add one middleware (or plugin) to a FastAPI app and every AI agent gets a
> deterministic, token-budgeted, hash-stable view of the deployed API — no
> LLM, no cloud, no extra bill.

Two integration surfaces, one codebase:

| Surface | What it is | Primary user |
| --- | --- | --- |
| `MarkdownizerMiddleware` (ASGI) | Serves read-only context endpoints under a prefix | Any FastAPI/Starlette app |
| `MarkdownizerPlugin` | Wraps the app, merges live route table into the IR, adds a `fastapi` context layer | Teams wanting an accurate API surface |
| `MarkdownizerRouter` (optional) | `APIRouter` variant for apps that prefer mounting to middleware | Same as middleware |

The upstream package stays untouched: this is a separate distribution that
depends on `markdownizer` and `fastapi`/`starlette`.

---

## 2. Background and Motivation

Upstream Markdownizer is a deterministic compiler:
`scan → parse → classify → Project IR → rank → budget → backends`.
It is static by contract: it never imports the target project (see
`docs/PROJECT.md` §1 and §3).

Static analysis has three blind spots that only a *running* FastAPI app can
close:

1. **Router composition** — `app.include_router(router, prefix="/v1")` means
   the real HTTP surface (`GET /v1/users/{id}`) exists only at runtime, not in
   any single file.
2. **Dependency graph** — `Depends(get_current_user)` security chains are
   invisible to `ast` heuristics and are exactly what agents need when asked
   "fix auth".
3. **Schema reality** — `response_model=UserOut` resolves after Pydantic model
   construction; the static IR only sees the class definition.

Conversely, the running app cannot see docstrings, comments, and unimported
modules; the static IR covers that. The product is the **join of both views**,
computed deterministically.

Upstream roadmap notes FastAPI/Pydantic detectors for 0.6.0. This project is
the runtime counterpart, and can later feed detector improvements back
upstream (see §13, Phase M4).

---

## 3. Goals and Non-Goals

### Goals

- One-line installation into any FastAPI app: middleware or plugin.
- Serve deterministic artifacts: `context.md`, `project.json`, `llms.txt`,
  `stats`, `manifest`, and (with the plugin) `routes.json`.
- Merge live route table into the context as an HTTP API layer, matched to IR
  symbols by `module + qualname`, never by guessing.
- Deterministic and hash-stable: same repo state → same `ETag` → cacheable.
- Secure by default: metadata-only, opt-in source, optional token auth,
  disabled in production unless explicitly enabled.
- Zero impact on the host app when the feature is off (pure passthrough).
- Python 3.9–3.13, matching upstream support.

### Non-goals

- No LLM calls, no embeddings, no telemetry (same contract as upstream).
- No OpenAPI replacement; `/openapi.json` stays FastAPI's job. We may
  *reference* it, never duplicate it.
- No hosted service, no persistence, no database.
- No modification of the host app's routes, responses, or startup behavior.
- No upstream IR schema changes (IR v1 is frozen; extensions live in this
  package).

---

## 4. Use Cases

1. **Agent context endpoint** — an AI agent (OpenCode, Claude Code, Cursor,
   Cline) fetches `GET /_markdownizer/context.md?q=auth&max_tokens=20000`
   from a staging deployment and works from a ranked, budgeted view.
2. **`llms.txt` for the API** — `GET /llms.txt` returns a Markdown index of
   packages, modules, and HTTP endpoints, consumable by browsing agents.
3. **CI drift check** — CI starts the app, fetches `project.json`, and diffs
   `hash` against the committed artifact; the context is a build product.
4. **On-call debugging** — `GET /_markdownizer/stats` shows top-ranked files
   and symbols; `?q=<stack trace symbol>` finds the owning code block.
5. **MCP-adjacent access before `markdownizer mcp` ships** — the HTTP surface
   gives agents a way to pull context without the MCP SDK.

---

## 5. Product Surface

### 5.1 Middleware (primary)

```python
from fastapi import FastAPI
from markdownizer_fastapi import MarkdownizerMiddleware

app = FastAPI(title="Shop API")

app.add_middleware(
    MarkdownizerMiddleware,
    project_root=".",
    prefix="/_markdownizer",
    include_source=False,
    profile="api",
    max_tokens=20000,
)
```

- Pure ASGI middleware (not `BaseHTTPMiddleware`) to avoid Starlette
  task-group/`contextvars` side effects and extra latency.
- Requests whose path does not start with `prefix` are passed through with
  **zero** scanning, parsing, or allocation beyond a path check.
- Endpoints are served in-process; artifacts are cached by IR content hash.

### 5.2 Plugin (route enrichment)

```python
from fastapi import FastAPI
from markdownizer_fastapi import MarkdownizerPlugin

app = FastAPI()
app.include_router(api_router, prefix="/v1")

plugin = MarkdownizerPlugin(app, project_root="src", include_source=False)
plugin.install()  # registers middleware + lifespan warmup
plugin.routes  # deterministic RouteInfo list
plugin.context_text()  # context.md + "HTTP API" layer
```

The plugin:

1. Builds the static IR via `markdownizer.build_project_ir`.
2. Walks `app.routes` and extracts `RouteInfo` records.
3. Matches each route to an IR symbol by `endpoint.__module__` →
   `ModuleInfo.name` and `endpoint.__qualname__` → `Symbol.qualified_name`.
4. Renders an "HTTP API" section appended after the standard context layers.
5. Serves everything through the same middleware endpoints plus `routes.json`.

### 5.3 Router (optional)

```python
from markdownizer_fastapi import MarkdownizerRouter

app.include_router(MarkdownizerRouter(project_root="src"), prefix="/_markdownizer")
```

For apps that cannot reorder middleware; functionally a thin wrapper over the
same artifact service. Documented as the fallback integration.

---

## 6. Architecture

### 6.1 Components

```
markdownizer_fastapi/
├── __init__.py          # public exports, __version__
├── config.py            # MarkdownizerConfig (validated, frozen dataclass)
├── service.py           # ArtifactService: build, enrich, cache, render
├── middleware.py        # MarkdownizerMiddleware (pure ASGI)
├── router.py            # MarkdownizerRouter (APIRouter fallback)
├── plugin.py            # MarkdownizerPlugin (route introspection + install)
├── routes.py            # RouteInfo extraction from app.routes
├── enrich.py            # deterministic join of IR symbols and RouteInfo
├── render.py            # HTTP API layer rendering (Markdown)
├── security.py          # authorizer, token, CIDR, enable-gating
└── py.typed
tests/
├── test_config.py
├── test_middleware.py
├── test_router.py
├── test_plugin.py
├── test_routes.py
├── test_enrich.py
├── test_security.py
└── test_determinism.py
```

Dependency direction is one-way: `middleware/router/plugin → service →
markdownizer` (upstream). No module ever imports host application code except
`app.routes` introspection performed by `plugin.py`/`routes.py`.

### 6.2 Data flow

```
         FastAPI app (live)                     source tree (static)
                │                                     │
   app.routes ──┤                             build_project_ir()
                ▼                                     │
         routes.py → RouteInfo[]          ir.py → ProjectIR (ranked on demand)
                │                                     │
                └────────────► enrich.py ◄────────────┘
                                 │
                        EnrichedProject
                                 │
                  render.py / optimize_context()
                                 │
                     service.py (hash-keyed cache)
                                 │
      middleware.py / router.py ─┴─► HTTP responses (md / json)
```

### 6.3 Determinism

- Static artifacts inherit upstream determinism (sorted scan, canonical
  `blake2b`).
- `RouteInfo` list is sorted by `(path, methods, symbol_id)`; methods sorted.
- The enriched hash is `blake2b(ir.hash + canonical route JSON)`; when the
  live route table is identical, the enriched hash equals the static IR hash.
- Same repository + same route table → byte-identical responses → stable
  `ETag`. Ranking results are excluded from the hash, as upstream does.

### 6.4 Caching and lifecycle

`ArtifactService` holds:

- `ir` — static ProjectIR;
- `routes` — live RouteInfo list (plugin only);
- `enriched_hash` — cache key;
- `lock` — `threading.Lock` around build/refresh.

Modes:

| Mode | Behavior |
| --- | --- |
| `lazy` (default) | Build on first artifact request; cache until process restart. |
| `startup` | Build in FastAPI lifespan/startup, so first request is fast. |
| `dev_reload` | Stat source files (`mtime` + size) on each request; rebuild when changed. |

No background threads, no file writes. The app is the cache.

---

## 7. HTTP API

Default prefix `/_markdownizer` (configurable). All endpoints are `GET` and
read-only. `HEAD` is supported via the same handlers.

| Endpoint | Response | Notes |
| --- | --- | --- |
| `GET {prefix}/manifest` | JSON | `hash`, `enriched_hash`, `ir_version`, `built_at`, counts. |
| `GET {prefix}/stats` | JSON (default) or text (`?format=text`) | Mirrors `markdownizer stats`. |
| `GET {prefix}/context.md` | Markdown | `?max_tokens=`, `?profile=`, `?rank=`, `?q=`. |
| `GET {prefix}/project.json` | JSON | Full IR + `file_ranks`; `routes` when plugin is on. |
| `GET {prefix}/routes.json` | JSON | Plugin only; 404 otherwise. |
| `GET {prefix}/llms.txt` | `text/plain` | Markdown index: packages, modules, endpoints. |
| `GET {prefix}/openapi-ref.json` | JSON | Redirect/link to the app's `/openapi.json`; no duplication. |
| `POST {prefix}/refresh` | JSON | Rebuild cache; requires auth; 404/405 when disabled. |

Response headers on every artifact:

```
ETag: "<enriched_hash>"
X-Markdownizer-IR-Version: 1
X-Markdownizer-IR-Hash: <hash>
Cache-Control: no-store            # when auth is enabled
Cache-Control: public, max-age=0, must-revalidate   # otherwise
```

`If-None-Match` matching the `ETag` returns `304 Not Modified`.

Errors use problem-style JSON: `{"error": {"code": "...", "message": "..."}}`
with `400` for bad parameters, `401/403` for auth, `404` when disabled, `503`
while the first build is in progress and `lazy=False, blocking=False`.

### 7.1 Route enrichment shape

```json
{
  "symbol_id": "src/api/users.py::UserView.list_users",
  "path": "/v1/users/{user_id}",
  "methods": ["GET"],
  "name": "list_users",
  "summary": "List users for the current tenant.",
  "tags": ["users"],
  "deprecated": false,
  "status_code": 200,
  "response_model": "UserOut",
  "dependencies": ["get_current_user", "get_db"]
}
```

Routes whose handler cannot be matched to an IR symbol are still listed, under
`"matched": false`, with the reason (`excluded`, `syntax_error`, `dynamic`).
Nothing is ever guessed or dropped silently.

---

## 8. Configuration Reference

`MarkdownizerConfig` (frozen dataclass, validated in `__post_init__`):

| Option | Default | Meaning |
| --- | --- | --- |
| `project_root` | `"."` | Root scanned by upstream Markdownizer. |
| `prefix` | `"/_markdownizer"` | URL prefix; normalized, must start with `/`. |
| `include_source` | `False` | `True` / `False` / `"signature"` (upstream `SourceMode`). |
| `include_comments` | `True` | Forwarded to upstream render options. |
| `profile` | `"api"` | Any upstream profile name. |
| `max_tokens` | `20000` | Default budget for `context.md`. |
| `rank_method` | `"pagerank"` | `pagerank` / `fanout` / `simple`. |
| `exclude` | `None` | Glob patterns forwarded to the scanner. |
| `include_routes` | `True` | Plugin-only; disable to serve static-only context. |
| `enable_env` | `"MARKDOWNIZER_FASTAPI_ENABLED"` | Env var that must be truthy for serving when `require_enable_env=True`. |
| `require_enable_env` | `True` | Production-safe default: 404 unless the env gate is set or `debug=True`. |
| `debug` | `False` | When `True`, bypasses the env gate and enables `dev_reload`. |
| `auth_token` | `None` | Bearer/`X-Markdownizer-Token` value; required for `POST /refresh`. |
| `allow_clients` | `None` | Optional CIDR allowlist (`["127.0.0.1/32", "10.0.0.0/8"]`). |
| `max_context_tokens` | `100000` | Hard ceiling for `?max_tokens=` overrides. |

Environment overrides (`MARKDOWNIZER_FASTAPI_PREFIX`, `..._PROFILE`, …) are read
once at construction; explicit kwargs win.

---

## 9. Security Model

Exposing internals over HTTP is the main risk. Rules:

1. **Opt-in by construction** — adding the middleware is already a choice, but
   `require_enable_env=True` means a deploy that forgets to disable it still
   returns `404` unless production explicitly sets the env var.
2. **Metadata by default** — `include_source=False`: signatures, docstrings,
   route table. Full source requires `include_source=True` plus auth.
3. **Auth** — constant-time token compare (`hmac.compare_digest`); optional
   CIDR allowlist; `POST /refresh` always requires the token.
4. **No path traversal** — endpoints take no file paths; the scanner is
   restricted to `project_root`; upstream already skips hidden files, VCS
   directories, and common noise (`.env` is never a `.py` file and is not
   read).
5. **No secrets in artifacts** — upstream extracts docstrings/comments/source
   only; the plan adds a test asserting no environment variable values appear
   in rendered output.
6. **Bound the work** — `max_context_tokens` ceiling; context builds are
   serialized under a lock; `dev_reload` stat pass is capped to the scanned
   file list.
7. **Observability** — one `logging` record per served artifact
   (`path`, `hash`, `bytes`, `client`), no request bodies logged.

---

## 10. Packaging and Compatibility

- Distribution: `markdownizer-fastapi`, import `markdownizer_fastapi`,
  versioned independently but pinned to the IR contract:
  `markdownizer>=0.4.5,<0.5` initially; widen after 0.5.0 is verified.
- Runtime dependencies: `fastapi>=0.100` and `starlette>=0.27` (transitively
  includes the ASGI types). Upstream `markdownizer` stays zero-dependency and
  is installed as a normal dependency of this package.
- `py.typed` shipped; `mypy --strict` clean; ruff with the same rule set as
  upstream (`E, F, I, B, UP, SIM`, line length 100).
- Python 3.9–3.13, CI matrix identical to upstream plus a FastAPI version axis
  (`latest`, `0.100` floor).
- Compatibility contract: HTTP endpoints, response shapes, and
  `MarkdownizerConfig` field names are the public API. Additive changes only
  until 1.0; breaking changes require a deprecation cycle.
- `llms.txt` remains available even if `include_source=True`; `llms.txt` never
  contains source by design.

### 10.1 Release and CI

- `ci.yml`: lint, format check, `mypy`, `pytest` with coverage gate 90%,
  package build, twine check — mirroring upstream conventions.
- `release.yml`: tag push → OIDC trusted publishing to PyPI.
- Optional: a `docs` job generated by running the middleware against this
  package's own demo app (dogfooding).

---

## 11. Testing Strategy

| Area | Tests |
| --- | --- |
| Config | Validation, env precedence, prefix normalization, frozen behavior. |
| Middleware passthrough | Non-prefixed requests untouched; no build triggered. |
| Endpoints | Each endpoint's status, content type, headers, `304` handling. |
| Budget | `?max_tokens=` respected within upstream ±10% slop; ceiling enforced; invalid values → 400. |
| Profiles/query | `?profile=` unknown → 400; `?q=` deterministic filtering. |
| Route extraction | `include_router` prefixes, multiple methods, dependencies, response models, unmatched routes. |
| Enrichment | Deterministic ordering; match by module+qualname; `matched: false` reasons. |
| Security | Env gate 404, token auth 401/403, CIDR allowlist, refresh auth, no env-value leakage. |
| Determinism | Two builds → identical bytes and `ETag`; enriched hash stable. |
| Concurrency | Parallel artifact requests under one lock; cache hit path. |
| Compatibility | FastAPI floor version test with `httpx`/`TestClient`. |

Fixtures: a small demo FastAPI app (two routers, one dependency, one Pydantic
response model, one excluded module) used across middleware, router, and
plugin tests.

Golden files: `context.md`, `routes.json`, and `llms.txt` snapshots for the
demo app, updated only via an explicit `--snapshot-update` flag.

---

## 12. Phases and Milestones

Estimates assume one maintainer, part-time.

### M0 — Scaffold (1–2 days)

- Package skeleton, `pyproject.toml`, dev extras, CI, ruff/mypy/pytest gates.
- `MarkdownizerConfig` + tests.
- Demo FastAPI app fixture.

**Deliverable:** installable package with green CI and no features.

### M1 — Middleware core (3–5 days)

- `ArtifactService` (lazy build, hash-keyed cache, thread lock).
- Pure ASGI `MarkdownizerMiddleware` with passthrough.
- Endpoints: `manifest`, `stats`, `context.md`, `project.json`, `llms.txt`.
- `ETag`/`304`, error shapes, budget validation.
- Determinism tests.

**Deliverable:** `pip install` + two lines of code → working context endpoints.

### M2 — Route enrichment (4–6 days)

- `routes.py` extraction from `app.routes`.
- `enrich.py` deterministic join, unmatched routing report.
- `render.py` HTTP API layer + `routes.json`.
- `MarkdownizerPlugin` with install/warmup; `MarkdownizerRouter` fallback.

**Deliverable:** context accurately reflects `include_router` prefixes and
dependency chains.

### M3 — Security and lifecycle (2–3 days)

- Env enable-gate, token auth, CIDR allowlist, `POST /refresh`.
- `startup` and `dev_reload` modes.
- Leakage and auth test suites; logging.

**Deliverable:** safe-to-deploy defaults, documented threat model.

### M4 — Release polish (2–3 days)

- README (usage, security, comparison), CHANGELOG, `docs/` page.
- OpenCode/Claude Code/Cursor snippets.
- `release.yml` OIDC wiring.
- Optional upstream contribution: FastAPI/Pydantic detector PR against
  `markdownizer` 0.6.0, and/or a proposal to allow profile registration via
  entry points so this package can ship a `fastapi` profile cleanly.

**Deliverable:** `0.1.0` on PyPI.

---

## 13. Acceptance Criteria

- `app.add_middleware(MarkdownizerMiddleware, ...)` works on FastAPI
  `>=0.100` and Python 3.9–3.13.
- Non-prefixed traffic shows no measurable overhead beyond a string prefix
  check (benchmark: middleware on/off, `wrk` or `pytest-benchmark`).
- Same repo + same route table → identical `context.md` bytes and `ETag`.
- Disabled by default in production (env gate) and metadata-only by default.
- Route enrichment matches `include_router` prefixes exactly on the demo app.
- Coverage `>= 90%`, `mypy --strict` clean, ruff clean.
- README quickstart works verbatim in a fresh venv.

## 14. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Exposing internals over HTTP | High | Env-gated default, metadata-only, token auth, CIDR option, no source unless opted in. |
| Middleware interferes with app | High | Pure ASGI passthrough; no `BaseHTTPMiddleware`; endpoint router isolated; contract tests. |
| First-request latency on big repos | Medium | `startup` warmup mode; lazy fallback; cache; benchmarks in CI on Django/FastAPI sample repos. |
| FastAPI/Starlette API drift | Medium | Pin floors, test latest + floor in CI, keep introspection behind `routes.py`. |
| Static IR vs live routes disagreeing | Medium | Explicit `matched: false` reporting; never guess; document the static counterpart. |
| Upstream IR changes break enrichment | Medium | Hash and `ir_version` checks at startup; fail closed with a clear error; pin `<0.5` initially. |
| Scope creep toward a docs site/UI | Medium | This is an API surface only; UI/HTML out of scope. |
| Duplicating MCP effort | Low | Position as HTTP-first; MCP can proxy these endpoints later. |

## 15. Open Questions

1. Prefix default: `/_markdownizer` (proposed) vs `/.well-known/markdownizer`.
2. Should `llms.txt` be served at the app root when the plugin is installed,
   or always stay under the prefix?
3. Source exposure: is `include_source="signature"` a better default than
   `False` for teams that want more value out of the box?
4. Separate distribution (proposed) vs `markdownizer[fastapi]` extra.
5. Should `POST /refresh` exist in 0.1.0, or only `dev_reload`?
6. Naming: `MarkdownizerPlugin` vs `FastAPIMarkdownizer`.

## 16. Out of Scope

- OpenAI/Anthropic/embedding calls, vector stores, telemetry.
- HTML documentation UI, Swagger customization, OpenAPI generation.
- Authentication backends beyond a static token and CIDR allowlist.
- Multi-language analysis (inherits upstream Python-first rule until 1.0).
- Persistence of artifacts (filesystem, Redis, database).
- Modifying upstream Markdownizer internals (only public API usage).

---

## Appendix A — Minimal README sketch

```python
from fastapi import FastAPI
from markdownizer_fastapi import MarkdownizerMiddleware

app = FastAPI()
app.add_middleware(
    MarkdownizerMiddleware,
    project_root=".",
    include_source=False,
)
```

```bash
curl http://localhost:8000/_markdownizer/context.md?profile=api\&max_tokens=20000
curl http://localhost:8000/_markdownizer/llms.txt
curl http://localhost:8000/_markdownizer/project.json | jq .hash
```

## Appendix B — Upstream APIs this plan depends on

- `markdownizer.build_project_ir(project_root, exclude=None) -> ProjectIR`
- `markdownizer.optimize_context(ir, max_tokens, profile, query, rank_method, prefer_tiktoken) -> OptimizedContext`
- `markdownizer.IR_VERSION`, `ProjectIR` (`packages`, `modules`, `symbols`, `imports`, `inherits`, `defines`, `stats`, `hash`)
- `markdownizer.optimizer.rank.rank_ir(ir, method)`
- `markdownizer.optimizer.profiles.get_profile(name)`
- `markdownizer.backends.RenderOptions` / `get_backend(name)`

All are public or stable-enough for a pinned dependency; none are forked.
