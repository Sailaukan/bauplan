# workflow.json

One file describes the agent at both levels. The overview reads `bands`, `nodes` and `edges`; the detailed view
reads each node's `detail`. `scripts/bauplan.py` checks it against the code and renders it.
`assets/example-workflow.json` is a complete small example (a support agent) to copy from.

## Top level

```json
{
  "title": "Support agent: ticket to reply",
  "repo": {
    "code_root": "src",
    "prompts_root": "prompts",
    "link": "https://github.com/OWNER/REPO/blob/{commit}/{path}#L{line}"
  },
  "drawn_at": { "commit": "4f2c9e1", "date": "2026-10-21" },
  "bands": [],
  "nodes": {},
  "edges": []
}
```

- `code_root`: every `at` and `file` path is relative to it; `""` for the repository root. `prompts_root`: every
  LLM `prompt` path is relative to it. Both are relative to the repository root.
- `link`: where a row's code opens. Placeholders: `{commit}`, `{path}` (from the repository root), `{line}`, and
  `{root}` (the repository's absolute path on this machine). Examples:
  - GitHub: `https://github.com/OWNER/REPO/blob/{commit}/{path}#L{line}`
  - GitLab: `https://gitlab.com/OWNER/REPO/-/blob/{commit}/{path}#L{line}`
  - A local editor: `vscode://file/{root}/{path}:{line}` or `cursor://file/{root}/{path}:{line}`
  Leave it out and rows show their path without a link.
- `drawn_at`: the commit the content was traced at. `check` reports the blocks whose files changed after it.
- `page_title` (optional): the browser tab's name; by default the part of `title` before the colon, plus "Workflow".

## Overview

`bands` are rows of work that share a unit: each request, each ticket, each document, once per batch. Each band
lists node ids left to right in `main`, and nodes hanging below one of them in `side`.

```json
{ "title": "Each ticket", "note": "One queued job per new ticket.",
  "main": ["webhook", "classify", "isUrgent", "draft"],
  "side": [{ "id": "escalate", "under": "isUrgent" }] }
```

`nodes` maps an id to a node. Kinds: `block` (a numbered step), `decision` (a question that changes the path),
`trigger` (where work starts), `side` (a branch off the main path), `person` (a step a person does), `output`.

```json
"classify": {
  "kind": "block", "num": 2, "title": "Classify the ticket",
  "items": [["IN", "subject, body, customer plan"], ["LLM", "Claude Haiku: category, urgency"], ["WRITE", "Ticket category"]],
  "file": "agents/classify.py",
  "detail": [],
  "handoff": { "data": "ticket id and category", "state": "Ticket status classified" }
}
```

- `items`: at most five `[TAG, text]` pairs, each under about 50 characters. Tags: `LLM`, `API`, `READ`, `WRITE`,
  `CODE`, `IF`, `PERSON`, `IN`, `OUT`.
- `handoff`: what the block passes to the next one, and the state of the main record at that point.
- `w` (optional): a node's width in pixels; blocks are 224 by default.

`edges`: `{ "from", "to", "kind", "label"?, "dashed"? }`. Kinds: `main` (left to right in a band), `down` (to a
side node below), `up` (from a side node below), `return` (from the end of one band to the start of the next).

## Detailed rows

`detail` is a list of rows in the order the code runs. Rows nest: a function's `calls` are what it calls, an `if`
has `yes` and `no` branches, a `switch` has `cases`, an `each` repeats its `calls`. The nesting is the structure.

| `t` | Fields | Use for |
| --- | --- | --- |
| `task` | `name`, `note` | where queued or scheduled work starts (a job queue, cron, a worker, a thread, a sub-agent run); everything after runs later, elsewhere |
| `fn` | `fn`, `returns`?, `at`, `note`?, `fx`?, `calls`? | a function call. `fn` is the call as written: `classify_ticket(ticket, model=FAST_MODEL)` |
| `do` | `title`, `at`, `match`?, `note`?, `fx`?, `calls`? | inline code that is not its own function: "Save the draft" |
| `if` | `cond`, `at`?, `then`?, `yes`?, `no`?, `side`?, `decided_by`? | a two-way condition. `then` is a short outcome when a branch just stops or returns |
| `switch` | `on`, `at`?, `cases`, `decided_by`? | a choice among several paths, such as the tool a model asked for. `cases`: `[{ "when", "rows", "side"? }]` |
| `each` | `over`? and/or `until`?, `at`?, `note`?, `calls` | a loop: `over` "each attachment", `until` "the model stops calling tools, at most 8 turns" |
| `end` | `text` | where a branch stops or hands over: "return None", "reply in 04 Send the reply" |

- `at`: `path:line` from `code_root`. For `fn` it is the line where the function is defined; `check` looks for the
  definition there in Python, JavaScript, TypeScript, Go, Ruby, Rust, PHP, Java, Kotlin, C#, Swift and similar.
  For `do`, `if`, `switch` and `each`, give `match`: a short string that is on that line, so `check` can find the
  line again after the code moves.
- `side`: on an `if`, `"yes"` or `"no"`, the branch that leaves the main path; on a switch case, `true`. Side paths
  are drawn faded so the main path stands out.
- `decided_by: "model"`: the path is chosen by a model's answer, not by code: a tool call, a routing or
  classification label, a "done" signal. It is drawn in the model's colour, because a reader must know where the
  model, rather than the code, steers the agent.
- Keep `note` to one sentence. Say what is not obvious from the name, in plain words.

Failures and concurrency, which readers most often get wrong, have their own fields so they stand out:

| Field | On | Use for |
| --- | --- | --- |
| `fail` | `if` (`"yes"`/`"no"`), switch case (`true`) | the branch that handles an error, a timeout, a missing answer. Drawn in red; the reader can show only failure paths |
| `retries` | `task` | how the queued work is retried: `"3 times, 60 s apart"`, `"none; the next scheduled run tries again"` |
| `lock` | any row | a lock it takes or holds: `"Order row (SELECT … FOR UPDATE)"`, `"Redis lock ticket:{id}, 30 s"` |
| `parallel` | any row, mostly `each` | what runs at the same time: `"4 pages at a time on threads"`, `"one job per document on workers"` |
| `risks` | any row | problems found while tracing, as plain sentences of what can happen: a race, a path that never runs, a setting nothing sets, a call held under a lock. Only what the code shows; they are listed on the Risks tab |

An agent loop where the model picks tools:

```json
{ "t": "each", "until": "the model answers without a tool call, at most 8 turns", "at": "agents/reply.py:41", "match": "for turn in range",
  "calls": [
    { "t": "fn", "fn": "client.messages.create(model, messages, tools)", "at": "agents/reply.py:43", "match": "messages.create",
      "fx": [{ "tag": "LLM", "title": "Claude Sonnet 5.5", "model": "claude-sonnet-5-5", "prompt": "reply-system.md" }] },
    { "t": "switch", "on": "What the model asked for", "decided_by": "model", "at": "agents/reply.py:52", "match": "block.name",
      "cases": [
        { "when": "search_docs", "rows": [{ "t": "fn", "fn": "search_docs(query)", "at": "tools/search.py:12" }] },
        { "when": "refund_order (only on paid plans)", "side": true, "rows": [{ "t": "fn", "fn": "refund_order(order_id)", "at": "tools/billing.py:30" }] },
        { "when": "no tool call: the answer", "rows": [{ "t": "end", "text": "leave the loop with the draft" }] }
      ] }
  ] }
```

## Effects (`fx`)

An effect is something a row does outside the code: a model call, an API call, a read or a write. Each one is drawn
in its column with an arrow from the row: into the database for a write, back to the code for a read, both ways for
a model or API call.

Every effect: `tag` (`LLM`, `API`, `READ`, `WRITE`), `title`, `text`? (one short line), `in`?, `out`?.

**READ and WRITE** cover databases, caches, vector stores, queues read as data, files and object storage.
- `title`: the table or store, its name first: `Ticket`, `Ticket + attachment`, `Redis cache`, `pgvector: chunks`.
  The text before ` + ` names the record in the Data view; set `entity` to name it explicitly instead.
- `fields`: the fields read or written: `["status", "category", "draft"]`.
- `sets`: status values this write sets: `{ "status": "classified" }`, or a list when the code picks one of
  several: `{ "status": ["sent", "escalated"] }`. Give it for every status, stage or state field; the Data view
  builds each record's lifecycle from these.

**LLM** covers chat and completion calls, embeddings when they are the point of the step, and any model that
returns a decision.
- `title`: the model as people say it (`Claude Haiku 5.5`, `GPT-5 mini`, `a fine-tuned classifier`), and
  `model`: the exact id.
- `prompt`: the prompt file under `prompts_root`, or `"inline"` when the prompt is built in code.
- `schema`: `{ "name": "TicketLabel", "fields": ["category: billing | bug | other", "urgent: bool"] }`.
- `feature`: the name the code logs usage under, so usage numbers can be joined. Use the logging name exactly.
- `unit` and `times`: how often it runs. `unit` is the band's unit (`ticket`, `document`); `times` is `"1"`,
  `"0–1"`, `"1 per attachment"`, `"1 per turn, at most 8"`.
- `threshold`?: the rule applied to the answer: `"confidence ≥ 0.7"`. `fallbacks`?: models tried when it fails.

**API** covers every other service: payment, email, search, CRMs, MCP servers, other agents.
- `title`: the service (`Stripe`, `Gmail API`, `MCP: github`). `method` and `endpoint`: `"POST"`, `"/v1/refunds"`.

## Usage numbers

`stats.json`, optional, beside `workflow.json`, keyed by `feature`:

```json
{ "ticket_classify": { "model": "claude-haiku-5-5", "calls": 412, "in_tokens": 1180, "out_tokens": 96,
                       "cost_usd": 0.0016, "ms_p50": 1900, "since": "2026-08-11" } }
```

Averages per successful call; `ms_p50` is the median duration in milliseconds. When the numbers were measured on a
different model than the one the code calls now, `model` says which, and the viewer shows it.

## Example runs

`examples.json`, optional, beside `workflow.json`: real runs laid over the detailed view, so a reader sees actual
values instead of descriptions. Take them from the project's own records (database rows, logs, traces, a test
fixture's recorded output); never invent a value. A list of:

```json
{
  "id": "ticket-48213",
  "name": "Ticket 48213, a refund request",
  "source": "production traces for ticket 48213, 3 Oct 2026; usage log for the same minute",
  "summary": "Classified billing at 0.91; 3 model turns, one refund_order call refused over the limit; sent to a person.",
  "path": { "classify:1.c1": "no", "draft:0.c0.c1": 2 },
  "rows": { "draft:0.c1": "8 turns not reached: answered on turn 3" },
  "fx": { "classify:0.c0#0": { "in": "subject “Charged twice”, plan pro", "out": "billing, urgent false, 0.91", "note": "812 tokens in, 0.9 s" } },
  "calls": { "ticket_classify": { "count": 1, "cost_usd": 0.0009 } }
}
```

- Keys are row keys: the block id, a colon, the row's index, then `.c<i>` for calls, `.y<i>` / `.n<i>` for the yes
  and no branches, `.k<case>-<i>` for switch cases. An effect adds `#<index in fx>`. `classify:0.c0#0` is the first
  effect of the first row called from the first row of block `classify`.
- `path`: the branch the run took at an `if` (`"yes"`/`"no"`) or a `switch` (the case index). Branches it did not
  take are faded.
- `rows` and `fx` notes: what this run did at that row or call. Keep each to one line.
- `calls`: model calls the run made, by `feature`, with their cost, for the Model calls tab.
- `check` reports keys an example names that the workflow no longer has, so examples go stale visibly.
