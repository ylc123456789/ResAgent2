# Unified Agent IO tool baseline

`tool_surface_678b03f.json` is retained as the historical baseline.
`tool_surface_unified_v2.json` records the intentional control changes:

- Runtime and Scientific finish now share the exact `report + artifacts` input schema.
- Question schemas preserve visible question background and enforce machine-safe answer field keys.
- Scientific question/work-request descriptions and content schemas match artifact-backed control delivery.

Only `runtime/ask_user`, `runtime/finish`, `scientific/ask_user`,
`scientific/finish`, and `scientific/request_work` have new fingerprints.
All 13 workspace, artifact, literature, environment and execution tool fingerprints
are unchanged. The baseline does not change automatically during tests.
