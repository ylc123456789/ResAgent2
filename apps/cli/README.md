# ResAgent2 CLI

This package is a thin command adapter over the existing `ResearchController`.
It does not contain research control, scheduling, Agent, or evidence logic.

## Commands

```bash
resagent2 run --workspace /path/to/repo --goal-file goal.txt
resagent2 show run_20260901_120000_ab12cd34
resagent2 answer run_20260901_120000_ab12cd34 \
  --workspace /path/to/repo --field primary_metric=accuracy
resagent2 resume run_20260901_120000_ab12cd34 --workspace /path/to/repo
```

`--goal` passes text through unchanged. `--goal-file` explicitly reads a UTF-8
file; the CLI never guesses whether a long goal is a path.

The default data root is `.resagent2/data`. Override it with
`RESAGENT2_DATA_ROOT` or `--data-root`.

The CLI creates one `ResourceLayout` from the selected data root and injects
it into both Coding and Experiment agents. Sequential tasks therefore share
the same managed environment and resource roots.

LLM configuration uses environment variables only:

- `RESAGENT2_MODEL` (default `deepseek-v4-flash`; set `deepseek-v4-pro` when needed)
- `RESAGENT2_API_BASE` (default `https://api.deepseek.com/v1`)
- `RESAGENT2_API_KEY_ENV` (default `DEEPSEEK_API_KEY`)
- the API key itself through the environment variable named above

Context capacity is explicit configuration rather than a provider lookup:

- `RESAGENT2_CONTEXT_WINDOW` (default `65536`)
- `RESAGENT2_RESERVED_OUTPUT_TOKENS` (default `4096`)
- `RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS` (default `1024`)
- `RESAGENT2_SCIENTIFIC_CONTEXT_TOKENS` (default `4096`)
- `RESAGENT2_CODING_CONTEXT_TOKENS` (default `4096`)
- `RESAGENT2_EXPERIMENT_CONTEXT_TOKENS` (default `4096`)
- `RESAGENT2_COMPILER_CONTEXT_TOKENS` (default `4096`)

The usable input budget is the smaller of the component limit and the model
window after reserving output, action-schema and safety-margin tokens. When
changing to a model with a smaller context window, set `RESAGENT2_CONTEXT_WINDOW`
to that model's documented value. The Workflow Compiler reuses the same context
composer and budget calculation but remains a stateless one-shot compiler; it
does not run through the Agentic Loop.

Existing resource and trace environment variables remain unchanged.

## Interactive shell

Run `resagent2` with no subcommand (or `resagent2 shell`) to enter an
interactive monitoring shell:

```text
resagent2> /run --goal "..." --workspace /path/to/repo
resagent2> /answer accuracy          # single requested field
resagent2> /answer metric=accuracy   # multi-field or explicit
resagent2> /resume run_xxx
resagent2> /show run_xxx
resagent2> /attach run_xxx           # read-only, watch persisted state
resagent2> /artifacts
resagent2> /trace [run_xxx]          # raw request/response/reasoning
resagent2> /quit
```

The shell is an observer of persisted Run state, not a second control plane. It
runs the three blocking controller methods on a background thread and renders
progress by polling the atomically-written `JsonRunStore` snapshot plus the
optional append-only LLM trace. Choose `--data-root` when starting the shell;
slash commands reject a second data-root so one shell cannot accidentally mix
two Run stores.

- **Ctrl-C stops watching and returns to the prompt; it does not cancel a Run.**
  If this shell's worker is active, it keeps running while the shell stays open;
  use `/attach` to watch it again. After a shell exit, resume an unfinished
  persisted Run explicitly with `/resume`.
- `/attach <run_id>` watches persisted state but never starts a worker. If no
  worker is advancing that Run, return with Ctrl-C and use `/resume` explicitly.
- The live view uses the metadata trace (agent/tool names). Raw request,
  response, and reasoning appear only through `/trace`, which needs
  `RESAGENT2_LLM_TRACE_LEVEL=full` and `RESAGENT2_LLM_TRACE_DIR`.

## Exit codes

- `0`: completed, or `show` succeeded
- `1`: failed or CLI error
- `3`: paused for user input
- `4`: still running
