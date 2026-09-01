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

LLM configuration uses environment variables only:

- `RESAGENT2_MODEL` (default `deepseek-chat`)
- `RESAGENT2_API_BASE` (default `https://api.deepseek.com/v1`)
- `RESAGENT2_API_KEY_ENV` (default `DEEPSEEK_API_KEY`)
- the API key itself through the environment variable named above

Existing resource and trace environment variables remain unchanged.

## Exit codes

- `0`: completed, or `show` succeeded
- `1`: failed or CLI error
- `3`: paused for user input
- `4`: still running
