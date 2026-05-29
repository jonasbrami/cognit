# 01 — Inline code context per question (anchors)

**Track A · PR A1 · feature #1 · foundation for #5, #9**

## Why

Today a question floats free of the code it probes. The reader has to hold the
diff in their head or context-switch to GitHub. The generation prompt already
encourages `file:line` references in prose (`system_generate.txt:11`), but that's
unstructured text — the UI can't act on it. Adding a structured `anchor` to each
question lets the browser show the exact diff hunk inline, and gives features #5
(drill the same hunk) and #9 (coverage map) a machine-readable target.

This is the spine of Track A: ship it first, the rest builds on the field.

## What

Add an optional `anchor` to every question type, teach the generator to emit it,
serve diff hunks to the browser, and render a collapsible hunk under each
anchored question. Optional throughout → old cached quizzes and questions without
anchors keep working unchanged.

## Design

### 1. Model (`src/cognit/engine/models.py`)

Add one model and an optional field on all four question types:

```python
class Anchor(BaseModel):
    path: str                              # repo-relative path from the diff
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> "Anchor":
        if self.end_line < self.start_line:
            raise ValueError(f"end_line {self.end_line} < start_line {self.start_line}")
        return self
```

Add `anchor: Anchor | None = None` to `MCQQuestion`, `MermaidQuestion`,
`OpenQuestion`, `TrueFalseQuestion`. Because it defaults to `None`:

- **Backwards-compat is automatic.** Old snapshot JSON (`state.py:_load`) and old
  cached quizzes without the field validate fine — no migration, no version bump.
  `Quiz.version` stays `"1"`.
- The discriminated-union `Question` type needs no change (still keyed on `type`).

Keep validation deliberately light: an anchor is a *hint*, not an assertion that
the lines are part of the diff. A question may legitimately anchor surrounding
unchanged context the reader needs. So **do not** reject anchors whose `path`
isn't in the changed-file set or whose lines fall outside a hunk — only the
shape/ordering above. (The coverage map in #9 treats "anchor.path ∈ changed
files" as *covered*; paths outside the diff simply don't mark anything.)

### 2. Validation (`src/cognit/mcp/validate.py`)

Pydantic already enforces the shape via the union parse in `validate_question`.
No new explicit checks needed — a malformed anchor surfaces through the existing
`malformed question: {e.errors()}` path and is handed back to the agent to fix.

### 3. Generation prompt (`src/cognit/engine/prompts/system_generate.txt`)

The generator already pulls hunks via `file_diff(path)` and is told to reference
`file:line`. Extend the output spec so it *also* emits the structured anchor when
a question targets specific lines. Add to the per-type field notes (near line
57–78) and the JSON examples:

> Every question SHOULD include an `anchor` when it probes specific code:
> `"anchor": {"path": "<a path from changed_files>", "start_line": N, "end_line": M}`
> — the line range (from the file's current/new-side numbering) the reader should
> look at. Omit `anchor` only for questions not tied to a specific location (e.g.
> a broad `open` rationale question). The anchor drives an inline code panel; it
> does not change grading.

Anchor emission is a **generation-time** behavior (one host turn), so it does
**not** depend on the Track B host-wake mechanism. This is why PR A1 is safe.

### 4. Serve hunks to the browser

The browser can't call MCP tools, and the web app currently has no access to the
diff — `_DiffProvider` is created inside `_build_mcp`'s closure (`server.py:121`).
Hoist it so both the MCP surface and the web app share one instance:

- In `server.py:main()`, construct `diffs = _DiffProvider(pr_url)` once and pass
  it into both `_build_mcp(state, llm, diffs)` and `_start_web(..., diffs=diffs)`.
- `build_web_app` (`web.py:36`) gains a `diff_section: Callable[[str], str]`
  param wired to `lambda path: do_file_diff(path, diffs.sections())` (reuse the
  existing `do_file_diff`, `server.py:74` — it already tolerates basename/suffix
  variants and refuses ambiguous matches).
- New endpoint `GET /diff?path=<path>` → returns the file's diff section as
  `text/plain` (or the existing "no changed file matches…" message). Caching is
  free: `_DiffProvider` already fetches once per process and is thread-safe.

This keeps the invariant intact: the browser reads diff text over HTTP from the
same process; the host still touches `QuizState` only via MCP tools.

### 5. UI (`assets/quiz_mcp.js`, `styles.css`)

For each question whose `/state` payload includes an `anchor`:

- Render a collapsible "📄 `path`:`start`–`end`" disclosure under the prompt,
  collapsed by default.
- On first expand, `fetch('/diff?path=' + encodeURIComponent(anchor.path))`,
  cache the result per path, and render the file's hunks.
- **DOM-built only** — build `<pre>`/`<span>` nodes and set text via
  `textContent`; never `innerHTML`. (Honors the existing textContent-only
  invariant; diff text is agent/repo-supplied.) Color +/- lines via CSS classes
  on per-line spans, not string-injected markup.
- v1 shows the file's changed hunks; highlighting the exact `start_line..end_line`
  band is a nice-to-have, not required for this PR.

## Files touched

- `src/cognit/engine/models.py` — `Anchor` model + optional field ×4
- `src/cognit/engine/prompts/system_generate.txt` — emit `anchor`
- `src/cognit/mcp/server.py` — hoist `_DiffProvider` into `main()`, thread to web
- `src/cognit/mcp/web.py` — `diff_section` param + `GET /diff`
- `src/cognit/mcp/assets/quiz_mcp.js`, `styles.css` — collapsible hunk panel

## Verification

- **Unit (`tests/mcp/`, `tests/engine/`):**
  - `models`: a question with a valid anchor round-trips; `end_line < start_line`
    raises; **a question dict with no `anchor` still validates** (backwards-compat);
    old snapshot JSON without the field loads in `QuizState` (extend
    `tests/mcp/test_state.py`).
  - `web` (`tests/mcp/test_web.py`): `GET /diff?path=<known>` returns the section;
    unknown path returns the "no changed file matches" message; uses a fake
    `diff_section` (no network).
  - `validate`: a malformed anchor comes back through the existing failures list.
- **e2e:** extend `tests/mcp/test_generation_e2e.py` to assert at least one
  generated question carries a well-formed anchor whose `path` is in
  `changed_files`.
- **Manual:** `cognit take <PR>`; confirm each anchored question shows the
  collapsible hunk, expands to the right file's diff, and that a quiz generated
  before this change (cached snapshot) still loads with the panels simply absent.
