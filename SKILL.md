---
name: bauplan
description: Bauplan draws how an AI agent's code works, as a block-diagram overview plus a detailed function-level view showing where every LLM call, tool call, external API call and database read or write happens, what goes in and comes out, the conditions, loops and model-made decisions, each record's status lifecycle, and the usage numbers of every model call, all checked against the code so it never quietly goes stale. Works for any language or framework. Use it whenever someone wants to see, explain, document, review or onboard onto how their agent, pipeline or LLM workflow works ("draw the workflow", "diagram the agent", "make a bauplan", "where do we call the LLM", "how does a ticket become a reply", "update the agent diagram", "is the diagram still right"), and after changing agent code in a repo that has docs/agent-workflow/workflow.json.
---

# Bauplan

The diagram exists so a developer who has never read the code can see what the agent runs, in what order, under
which conditions, where a model rather than the code steers it, what each model is sent and what it returns, and
which records change, and can trust that it matches the code today.

It is one file, `docs/agent-workflow/workflow.json`, rendered into `docs/agent-workflow/index.html` with five views:
- **Overview**: 6 to 14 numbered blocks in bands, one band per unit of work (each request, each document, once per batch).
- **Detailed**: each block's functions in the order they run, nested by call, condition and loop, with every call
  drawn as an arrow into its column (LLM, external API, database and files). Side paths are faded.
- **Data**: every table and store, the status values it moves through, and every step that reads or writes it.
- **Model calls**: every model call grouped by unit of work, with how often it runs and its usage numbers.
- **Risks**: problems found while tracing, each linked to its row.

The detailed view can show all paths, the main path only, or failure paths only, and can lay a real example run over
the diagram: the values it saw, the branches it took, the model calls it made.

Read `references/spec.md` before writing any of it: the format is strict, and the viewer and the check depend on
it. `assets/example-workflow.json` is a complete small example to start from.

## Steps

1. **Start from what exists.** If `workflow.json` is there, run the check first (step 6). Retrace only the blocks it
   reports as changed or broken; keep the rest.
2. **Find where work starts and follow it.** HTTP handlers, webhooks, queue consumers, scheduled jobs, CLI
   commands, chat entry points. Follow the main unit of work end to end by reading the code, never by guessing from
   names or framework conventions. Note every place it crosses a boundary: a queued job, a model call, a tool call,
   an external service, a write, a person.
3. **Write the overview.** Bands by unit of work. Block titles say what happens ("Classify the ticket"), not what the
   class is called. At most five short items per block. A `decision` only where the path really splits; a `side`
   node for a branch that leaves the main path.
4. **Write each block's detail.** These rules are what make it readable:
   - Rows in the order the code runs. Nest them: `calls` for what a function calls, `yes`/`no` or `cases` for a
     choice, `each` for a loop. Rows that run only under a condition sit inside it; that nesting is the structure.
   - Mark every choice a model makes with `decided_by: "model"`: tool selection, routing labels, a "done" signal,
     a classification that picks the path. Agent loops are an `each` with `until`, holding the model call and a
     `switch` over what it asked for.
   - One row per thing a developer must know about: a decision, a call out, a write, a handoff to a queue or
     another agent. Fold formatting, logging and small getters into a note.
   - Every model call carries its model id, prompt file, output schema, logged feature name, how often it runs,
     the rule applied to its answer and its fallbacks. Every API call its method and endpoint. Every read or
     write its fields, and `sets` for every status, stage or state value, so the Data view can show the lifecycle.
   - Mark the branch that leaves the main path with `side`, and end stopped paths with an `end` row.
   - Mark what readers most often get wrong: `fail` on branches that handle errors, `retries` on queued work,
     `lock` where a lock is taken or held, `parallel` where work runs at the same time. A developer reading the
     failure paths alone should learn what happens when the model, a service or a worker fails.
   - Record `risks` you find while tracing: races, paths that can never run, settings nothing sets, slow calls held
     under a lock. Verify each one in the code before writing it down, and say what can happen, plainly.
   - Check each `at` as you write it (`grep -n`), and give `match` text on rows that are not function definitions.
     Notes are one plain sentence saying what the name does not.
   - For a large agent, split the blocks across parallel subagents. Give each this file, `references/spec.md`, a
     finished block as an example, the exact block boundaries and an output path; then merge their JSON into
     `workflow.json` and remove anything two of them both traced.
5. **Add usage numbers if they exist.** If model calls are logged with a name per call site (a usage table, an
   observability tool such as Langfuse, LangSmith, Helicone or OpenTelemetry traces, or provider exports), write
   `stats.json` beside the workflow in the format in `references/spec.md`, with a small script kept next to it so
   the numbers can be refreshed. Say which model they were measured on. Skip this when nothing is logged.

   **Add one or two real example runs if records exist.** Pick a typical run from the project's own data (database
   rows, logs, traces) and write `examples.json`: the branches it took, what it sent to and got from each model
   call, what it wrote, and its model calls per feature. Say where every value came from; never invent one. A real
   run is what turns the diagram from a description into something a developer can check against reality.
6. **Check against the code.**
   `python3 <this skill's folder>/scripts/bauplan.py check docs/agent-workflow/workflow.json`
   Repeat until nothing is "not found". `--fix` rewrites line numbers of rows that only moved; a function that is
   gone means that part of the agent changed, so retrace it.
7. **Render and look.** `python3 <this skill's folder>/scripts/bauplan.py render docs/agent-workflow/workflow.json`,
   then open `docs/agent-workflow/index.html` and look at it once (a screenshot is enough) for clipped or
   overlapping text. To publish it somewhere that adds its own `<html>` and `<head>`, render with
   `--fragment --out <path>`.
8. **Record what it was drawn from.** Once the content matches the current code, set `drawn_at` to the short HEAD
   commit and today's date. The check compares against that commit to say which blocks changed since.

Tell the developer what the tracing turned up that looks wrong or surprising: dead settings, races, paths that can
never run, model calls that run more often than expected. A diagram drawn from the code finds these as a side effect.

## Keeping it true

- After changing agent code, run the check. It names the blocks whose files changed since `drawn_at`; retrace
  those, then update `drawn_at`.
- `check` exits 1 when a row or a prompt file is not found, so it can run in CI or a pre-commit hook.
- Never fill a gap with a guess. If part of the code is unclear, read more of it, or leave the row out and say so.
- The scripts use only the Python 3 standard library. Without git, the check still verifies every row; it just
  cannot say what changed since the diagram was drawn.
