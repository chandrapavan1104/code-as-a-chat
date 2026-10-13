# Gajala model catalogs

The coding picker reads the signed-in CLI catalogs: Claude's stream-json
initialization response (including resolved alias IDs), Codex app-server
`model/list` with pagination, Gemini ACP's `session/new` model metadata, and
Ollama's installed tags. No inference prompts are sent during discovery.
Neutral temporary working directories avoid loading project instructions.

`GET /api/model` preserves the existing presets and adds catalog source,
observation time, stale/error status, resolved Claude IDs, and separate routing
configuration. `?refresh=true` refreshes on demand. Successful reads last fifteen
minutes; failures retry after one minute, retaining a marked stale observation
for at most a day. No static model IDs are inserted into a live catalog.

The picker applies to coding runs. Everyday routing uses SHELL_MODEL and the
OpenAI API fallback uses OPENAI_SHELL_MODEL. CLI subscription models and API
models have different access and billing; an API model announcement does not
establish that a coding subscription can run it. Explicit pins remain intact.
Absent saved models are shown as absent instead of silently migrated. Gemini
exact model IDs survive resolution. Codex's unpinned default comes from its CLI
unless CODEX_MODEL is explicitly configured.

Keep the provider CLIs updated: new models can require a newer CLI. Catalog
refresh synchronizes models available to the installed, signed-in client; it
cannot grant account access or install future CLI updates. Refresh does not
select the newest or most expensive model on the owner's behalf.

Incident: run 83b69a09b6014bd9 used Claude Sonnet, then an empty exception at the
60-second router deadline caused OpenAI gpt-4o-mini fallback. The home action
succeeded and its device read was confirmed. Sonnet's native alias resolved to
claude-sonnet-5-5 during inspection. The evidence supports a routing timeout,
not a retired Sonnet ID. Routing now allows 120 seconds, and timeout errors
include the duration. Earlier validation rejections also caused fallbacks.

Live verification after updating Codex 0.154.0 → 0.162.1 and Gemini CLI
0.49.0 → 0.63.0: all four catalogs refreshed successfully. Codex now lists
gpt-6.1-sol plus the GPT-6 Astra/Sol/Luna and older 5.6 models; Gemini lists
gemini-3.8-flash and gemini-3.5-flash-lite alongside its Pro/Auto choices.
Claude resolves Sonnet to claude-sonnet-5-5; a synthetic Sonnet call succeeded.
Signed Android update: 1791843632. The saved Codex Astra pin was preserved.
