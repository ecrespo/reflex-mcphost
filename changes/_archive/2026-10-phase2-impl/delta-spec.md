# Delta spec — 2026-10-phase2-impl

> Status: `APPROVED` (owner's standing instruction to implement without per-phase approval,
> 2026-10-31) · folded 2026-10-31 · Source: deviations reported while implementing Phase 2
> (F2-01…F2-10). Constitution unaffected.

Every item below is now the current truth; the most user-visible ones are applied verbatim
in the API, DM, TD and Plan (see "Applied edits"). The full list is kept for traceability.

## Applied edits

- **API 1.5 → 1.6**: §4.1 Host test seams (`platform`, `env`, `keyring_backend`) and
  lifecycle rules; §4.2 `HostConfig` extra fields; §4.3 constructor, `config_errors()`,
  `on_diff`, rules (no private template; `set_enabled` by effective origin; templates
  written back; strict validation before write; user-file errors raise); §4.5 state table
  row `reconnecting → auth_required`, last-known connection fields; §4.10 `TrustRequest`
  fields `destination`, `url_template`, `resolved_command` (replacing `url`) and the wider
  risky-env list, case-insensitive.
- **DM 1.3 → 1.4**: §2.4 stdio fingerprint includes `resolved_command`; §2.5 stdio cache key
  uses a keyed hash of env names **and values**.
- **TD 1.4 → 1.5**: Flow B step 6 — catalog refresh runs right after the `ready` transition;
  shutdown shielded from Client entry with a 10 s per-Client budget; P2-01 detection via an
  httpx response hook.
- **Plan 1.3 → 1.4**: new task F8-08 (known gap below).

## Known gap (tracked)

REQ-CON-008: the SDK signals the stdio Server's process group only when it escalates
(terminate/kill). A Server that exits promptly on stdin EOF but leaves children in its
group is not cleaned up. Plan F8-08 closes it (kill the remaining process group after a
clean exit, needs the child pid).

## Deferred

REQ-CON-013 (`legacy_sse_fallback`, COULD): not implemented in Phase 2; a `legacy_sse`
definition connects over Streamable HTTP. Revisit after 0.1.0.

## Full deviation list

## Batch F (F2-01..F2-04)
1. HostConfig extra fields: config_path (default default_user_file()), cwd, problems, sampling: SamplingConfig; load/aload take env kwarg.
2. HttpTransport.legacy_sse: bool=False (VS Code/Cursor type "sse"); add/update with legacy_sse=True → ConfigError (own TOML has no SSE transport).
3. ServerDefinition.origin defaults to ConfigOrigin(mcphost_user, path=None, precedence=2) for API-created; timeout_s must be > 0.
4. ServerRegistry(config, *, platform=None, env=None, events=None); async load() (no diff/event); config property; config_errors() -> frozenset[ServerId]; on_diff list of async callbacks awaited after reload() and every mutation.
5. reload() re-reads the user file; missing → no own servers; invalid → ConfigError, registry unchanged.
6. RegistryDiff "changed" = definition differs (SecretStr equality covers env/header values) OR config_error flag flips. Drop "unexpanded template kept privately" from API §4.3.
7. set_enabled: own effective definition → edit [servers.<id>].enabled; third-party or project mcphost.toml (incl. own shadowed by project) → [overrides.<id>].
8. update() writes back ${VAR} templates when they still expand to the new value (expanded secrets never persisted). Mutations validated with the strict parser before writing.
9. User-level own file (default or explicit) invalid/unknown keys/bad schema_version → ConfigError (clarifies API §4.1 "explicitly given"). Missing default file = defaults. Project mcphost.toml problems never raise.
10. ${workspaceFolder} expands only in VS Code-style files; elsewhere unsupported_variable.
11. Third-party type values accepted: stdio, http, streamable-http, streamableHttp, sse; entries may carry cwd. disabled:true → enabled=False.
12. Files decoded as utf-8-sig (BOM accepted); unreadable/non-UTF-8 third-party → parse_error.
13. invalid_server_id problems have server_id=None, id named (truncated 80 chars) in message.
14. Extra module core/config/common.py; mcp_json.parse_extra auto-detects mcpServers/servers.
15. Exceptions renamed UnresolvedVariableError / UnsupportedVariableError (ruff N818).
16. F2-05a: SecretStore(data_dir, db, *, keyring_backend=None) get/set/delete; InstallKey.keyed_hash; load_or_create_install_key(store).

## Batch G (F2-05b, F2-06, F2-07)
17. TrustStore(db, key).require_trust(definition, prompter); DB lookup failure → untrusted, prompter not asked; store failure after confirm → trusted for this launch, re-ask next time. Fingerprint includes "transport"; cwd str|None; origin_path "" when none.
18. CommandNotFoundError(MCPHostError) in connection/transports.py; executable resolved with cwd + definition PATH.
19. build_transport(definition, *, on_stderr=None, http_transport=None) → single-use ObservedTransport(closed, auth_challenge).
20. LogEntry(level, logger, timestamp, text redacted) in connection/stderr.py; stderr lines level info, logger "stderr", capped 64 KiB + "[truncated]". LogRing(maxlen, *, on_entry, clock).
21. ServerStatus in state.py; era/protocol_version/server_info/capabilities keep last-known values after leaving ready.
22. ServerSupervisor surface (start/connect/stop/reconnect/aclose/wait_for/lease/status/logs); on_ready hooks run after ready transition (Flow B step 6 order changed: catalog refresh after ready, entries appear stale-free when fetched).
23. Failure reasons: transport_error: <ExceptionType>, transport_lost, unsupported_protocol_version: requested X, supported Y, internal_error.
24. reconnect() from ready emits ready→disconnected then disconnected→connecting.
25. (decided) API §4.5: add edge reconnecting → auth_required on 401 with auth metadata (token expiry during reconnect) — implemented in batch H.
26. (decided) TrustRequest.url = unexpanded template from the file — HttpTransport keeps the template privately; implemented in batch H.

## Batch H (F2-08, F2-09, F2-10)
27. API §4.5 table + diagram: add RECONNECTING → AUTH_REQUIRED.
28. TrustRequest: `url` replaced by `destination` (scheme://host[:port] of expanded URL) + `url_template` (file template, userinfo stripped; None for API-built); new `resolved_command` (absolute executable), part of stdio fingerprint {transport, command, resolved_command, args, cwd, origin_path, env}. DM §2.4 update.
29. RISKY_ENV_KEYS += PATH, PATHEXT, NODE_PATH, PYTHONHOME, PYTHONUSERBASE, GIT_SSH_COMMAND, LD_AUDIT, DYLD_FRAMEWORK_PATH; case-insensitive matching. API §4.10 list update.
30. HttpTransport private url_template (excluded from eq/hash).
31. Era cache stdio key: env via keyed_hash({name: value}) (DM §2.5 said env_keys); identical process configs share a row.
32. Cached modern entry is validated by one declared list request before ready; MCPError other than CONNECTION_CLOSED = era-indicating → drop + retry auto, no failure counted. Era-indicating errors on later requests not watched.
33. Forced mode="modern": pins 2026-07-28, send_discover()+adopt() when no cache; mode="legacy" → SDK legacy. Override skips cache read but writes it.
34. P2-01 detection via httpx response hook (404 to POST carrying Mcp-Session-Id); rebuild reruns on_ready; server.log logger "host"; lease() raises ServerUnavailableError(state=ready) during rebuild.
35. Shutdown: Client body shielded from entry; interrupt event ends it; SHUTDOWN_BUDGET_S = 10 per Client.
36. KNOWN GAP REQ-CON-008: SDK signals the process group only when escalating; a server exiting promptly on stdin EOF but leaving children in its group is not cleaned. → hardening task F8 (needs child pid / own process-group kill).
37. Host(config=None, *, prompter=None, sampling_provider=None, platform=None, env=None, keyring_backend=None); members config, database, prompter; RuntimeError before start / second start; HostClosedError after aclose; start/aclose same task.
38. ConnectionManager internal methods; connect/disconnect/reconnect no-ops for disabled/config_error; wait_ready raises immediately on terminal states; authorize typed -> None (NotImplementedError until Phase 4); HostClosedError after aclose.
39. disabled/config_error are parked entries (no supervisor); era cache pruned with retain(current definitions).
40. Lazy PEP 562 re-exports in reflex_mcphost/__init__.py.
41. REQ-CON-013 legacy_sse_fallback (COULD) deferred.

## Batch I (F2-11…F2-14)
42. on_ready hooks run concurrently for the whole ready period and are cancelled when the connection ends.
43. ServerSupervisor kwarg `message_handler` (legacy list changes); Client built with `input_required_max_rounds=8`.
44. `catalog.changed` (all lists) also published when entries go stale and when a removed Server is dropped (`Catalog.forget`).
45. A resources list change re-fetches `resources` and `resource_templates`.
46. Keep-first-and-warn duplicates also for templates/prompts (by name) and resources (by URI).
47. Legacy `list_changed` acted on even if `listChanged` was not declared; modern listen opts in only to declared kinds.
48. Listen-reopen backoff resets after every acknowledged open.
49. TTL refresh (REQ-CAT-013) for unwatched lists with ttlMs > 0, ≥ 30 s; legacy results carry no TTL.
50. Catalog reads: `InputRequiredRoundsExceededError` → `InputRequestError("input_rounds_exceeded")`; an undeclared input request surfaces as `ServerRequestError` until F3-10.
51. Legacy subscribe/unsubscribe and the rebuild ping use `client.session.send_request/send_ping` (avoids SDK deprecation warnings).
52. Session-404 rebuild bounded: > 3 rebuilds without a successful ping or 30 s uptime → `reconnecting` (reason `transport_error: session_terminated`); such a Server cycles with backoff and never reaches `failed` (failure counter resets on each ready).
53. Trust url_template authority also ends at `\`; url_template = None when its literal host differs from the destination host.
54. Pagination capped at 1000 pages; a repeated cursor stops the fetch with a warning.
55. REQ-CAT-010 (COULD) and REQ-CAT-013 (SHOULD) implemented; only REQ-CON-013 (COULD) deferred.
