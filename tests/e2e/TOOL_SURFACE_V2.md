# Unified Agent IO tool baseline

`tool_surface_678b03f.json` is retained as the historical baseline.
`tool_surface_unified_v2.json` records the intentional control changes:

- Runtime and Scientific finish now share the exact `report + artifacts` input schema.
- Question schemas preserve visible question background and enforce machine-safe answer field keys.
- Scientific question/work-request descriptions and content schemas match artifact-backed control delivery.

For the schema 12 migration, only `runtime/ask_user`, `runtime/finish`, `scientific/ask_user`,
`scientific/finish`, and `scientific/request_work` have new fingerprints.
All 13 workspace, artifact, literature, environment and execution tool fingerprints
are unchanged. The baseline does not change automatically during tests.

Schema 13 updates the embedded public schema constants and adds `coding/delete_path`
to the current fixture. The other tool schemas and descriptions remain unchanged;
execution-time environment auditing changes behavior, not the tool input shape.
`test_delete_invoke.py` additionally compares the Coding action names with the tools
actually advertised through the native protocol.

Schema 14 only updates embedded public schema const/default values. Before
updating the fixture, normalizing those exact values back to 13.0 reproduced
every schema-13 fingerprint. No tool names, parameters or descriptions changed.

Schema 21 adds `shared/fetch_literature_fulltext` for a registered paper's original
PDF and extracted text. `shared/literature_search` now describes independent paper
metadata/abstract artifacts and a separate query receipt. Its input fields are
unchanged. Scientific `ask_user`/`request_work` describe registered citations,
without making reading logs a control gate. Shared finish and Scientific control
schemas embed version 21.0. These are the five changed fingerprints plus the one
new tool; no other existing tool fingerprint changed.
