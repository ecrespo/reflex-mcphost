# Delta spec — 2026-11-cimd-url

> Status: `APPROVED` (owner decision, 2026-11-01) · folded 2026-11-01 · archived.
> Source: owner decision relayed by the orchestrator: the Client ID Metadata Document URL
> (REQ-AUTH-012) changes from `https://ecrespo.github.io/reflex-mcphost/oauth/client.json` to
> `https://blog.seraph.to/reflex-mcphost/oauth/client.json`, frozen from `0.1.0` on.
> Reason (verified with `curl`, 2026-11-01): the owner's GitHub user Pages site has the
> custom domain `blog.seraph.to`, so the `github.io` address answers `301` →
> `https://blog.seraph.to/reflex-mcphost/oauth/client.json`; a CIMD `client_id` must be
> exactly the URL the authorization server fetches (no redirect). Nothing was released with
> the old URL, so no installed Host depends on it.
> Constitution unaffected. Data Model unaffected.

## Items

1. **PRD REQ-AUTH-012 — MODIFIED.** "a stable HTTPS URL (GitHub Pages of
   `ecrespo/reflex-mcphost`)" → "a stable HTTPS URL that answers without a redirect (GitHub
   Pages of `ecrespo/reflex-mcphost`, served under the owner's custom domain:
   `https://blog.seraph.to/reflex-mcphost/oauth/client.json`, frozen from `0.1.0` on)". Same
   REQ id and meaning (stable, versioned, loopback-only redirect URIs).
2. **API §4.2 `HostConfig.oauth_client_metadata_url` default, §5.2 schema example —
   MODIFIED** to the new URL.
3. **Code — MODIFIED.** `core/config/models.py` `DEFAULT_OAUTH_CLIENT_METADATA_URL`;
   `docs/oauth/client.json` `client_id` (its only self-referencing URL; `client_uri` stays
   the GitHub repository); `.github/workflows/pages.yml` header comment;
   `tests/contract/test_mcp_sdk_auth_surface.py` (the CIMD-URL validity example). Test
   first: `tests/unit/test_cimd_document.py::test_client_id_is_the_non_redirecting_custom_domain_url`
   (the constant equals the new URL, the document's `client_id` equals the constant, the
   host is not `ecrespo.github.io`) — failed before the change. The existing
   `test_document_is_valid_client_metadata` already asserted `client_id ==
   DEFAULT_OAUTH_CLIENT_METADATA_URL`; kept. No `ecrespo.github.io` reference remains in
   `src/`, `docs/`, `.github/`, README, CHANGELOG or the current specs (only in archived
   deltas / Analyze reports, which are history, and in the new test's negative check).
4. **TD §9.1 — ADDED.** The URL, why (custom domain → `301`), `404` until `pages.yml` first
   runs on `main` (the release merge), F8-07b's live check, and the residual below.
5. **Tasks F4-10 text, F8-07b status and Done — MODIFIED.** F8-07b checks the URL is live
   before the tag: `200`, `application/json`, no redirect, body = `docs/oauth/client.json`.
   Plan §2 GitHub row names the URL.
6. **README** (OAuth paragraph) and **CHANGELOG `[0.1.0]`** (Added) name the URL.

## Risk (recorded: PRD §8 dependency row, §11 risk table, TD §9.1 residual)

- The URL now depends on the owner keeping `blog.seraph.to` registered and mapped to the
  user Pages site. If the domain lapses or the mapping changes after `0.1.0`, CIMD breaks for
  **every** install at once (authorization servers cannot fetch the document, or fetch it
  from someone else). Fallbacks that keep working: Dynamic Client Registration (where the AS
  offers it) and a configured `client_id` in `mcphost.toml` (REQ-AUTH-003). Changing the URL
  after `0.1.0` is a breaking change (new release + Delta Spec).
- Until the release merge deploys Pages, the URL answers `404`; a Host built from `develop`
  falls back to DCR / configured `client_id` against a CIMD-only AS.

## Applied edits

- **PRD 1.10 → 1.11**: REQ-AUTH-012; §8 hosting dependency row; §11 domain-lapse risk;
  change history.
- **API 1.18, TD 1.18, Plan 1.11, Tasks 1.19**: shared with `2026-11-skip-prereleases` /
  `2026-11-final-gaps` (one bump per document; change-history rows name all deltas);
  related-PRD versions → 1.11 in API, TD and Plan. Data Model not edited (its header keeps
  PRD 1.10 — only edited documents are bumped).
- **Archived delta `2026-11-phase8-impl`**: gap G-7 restated.

## Constitution check
- Art. 5 — the public document still carries no secret-like member (test unchanged).
- Art. 7 — the URL change is pinned by a test that failed first.
- Art. 9 — recorded in PRD, API, TD, Plan, Tasks and CHANGELOG before the URL is frozen.
- Exceptions requested: none.
