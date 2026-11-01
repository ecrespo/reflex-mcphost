"""Security walk-through of the Technical Design §6.1 attack surface (F8-01; REQ-NFR-010,
REQ-NFR-011, REQ-NFR-012, Art. 5).

``EVIDENCE`` maps every §6.1 vector (by a unique prefix of its first column) to the tests
that prove its mitigation (``path::function``), and ``NOTES`` says what no automated test
can cover (residual risk, owner-only checks). The table is parsed from the spec: a row
without evidence, a key matching no row (or several), or a cited test that does not exist
fails the build — adding a §6.1 row means adding its evidence here.

Two rows had no test before F8-01 and get one in this module: the SSE event-size limit
(the SDK default is kept) and the release path (actions pinned to commit SHAs,
``id-token: write`` only on the tag-gated ``publish`` job in environment ``pypi``).
"""

from __future__ import annotations

import ast
import inspect
import re
from functools import cache
from pathlib import Path

from mcp.client import streamable_http

REPO = Path(__file__).resolve().parents[2]
TD = REPO / "specs" / "technical" / "mcphost-architecture.md"
SRC = REPO / "src" / "reflex_mcphost"
WORKFLOWS = REPO / ".github" / "workflows"
HERE = "tests/security/test_threat_model.py"

E2E_APPS = "tests/e2e/test_mcp_apps.py"
PROXY = "tests/unit/ui/proxy/test_server.py"
GUARD = "tests/security/test_loopback_guard.py"
REDACTION = "tests/security/test_redaction.py"
KEYRING = "tests/integration/resilience/test_keyring_failure.py"

EVIDENCE: dict[str, tuple[str, ...]] = {
    "Malicious project config": (
        "tests/unit/core/test_trust.py::test_first_launch_asks_once_second_does_not",
        "tests/unit/core/test_trust.py::test_changes_re_ask",
        "tests/unit/core/test_trust.py::test_http_project_trust",
        "tests/unit/core/test_trust.py::test_http_changes_re_ask",
        "tests/unit/core/test_trust.py::test_risky_env_list_matches_api",
        "tests/unit/core/test_trust.py::test_stored_row_has_no_env_value",
        "tests/integration/connection/test_supervisor.py::test_untrusted_project_stdio_never_spawns",
        "tests/integration/connection/test_supervisor.py::test_untrusted_project_http_makes_no_request",
        "tests/integration/test_host.py::test_untrusted_project_server_fails_others_ready",
    ),
    "Project `mcphost.toml` changing host settings": (
        "tests/unit/core/config/test_parsers.py::test_project_settings_are_ignored_one_problem_per_key",
    ),
    'Tool definition "rug pull"': (
        "tests/unit/core/consent/test_index.py::test_reference_model",
        "tests/unit/core/test_invoker_toctou.py::test_changed_definition_after_consent_is_denied",
        "tests/unit/core/test_invoker_toctou.py::test_removed_tool_after_consent_is_not_found",
        "tests/integration/test_invoker_toctou.py::test_definition_change_during_prompt_is_denied",
        f"{REDACTION}::test_phase8_payloads_carry_no_argument_values",
    ),
    "Prompt-injection via tool descriptions": (
        "tests/architecture/test_annotation_hints.py::test_no_core_module_reads_annotation_hints",
        "tests/unit/core/test_catalog.py::test_annotations_are_stored_as_given",
        "tests/unit/ui/test_reflex_prompter.py::test_consent_cards_show_the_request_and_its_options",
        "tests/unit/core/consent/test_policy.py::test_headless_denies_without_rule",
    ),
    "Agent self-escalation": (
        "tests/unit/core/consent/test_index.py::test_other_consumers_rule_never_matches",
        "tests/unit/core/consent/test_policy.py::test_human_answer_scopes_rule_to_requesting_agent",
        "tests/integration/toolset/test_toolset.py::test_call_is_consented_and_audited_per_consumer",
        "tests/integration/handlers/test_sampling.py::test_rule_for_agent_a_does_not_apply_to_b",
    ),
    "MCP App escaping to Host origin": (
        f"{E2E_APPS}::test_view_and_proxy_are_isolated_from_the_ui_origin",
        f"{E2E_APPS}::test_view_frames_csp_and_handshake",
        f"{E2E_APPS}::test_localhost_ui_gets_the_notice_and_no_app",
        f"{PROXY}::test_page_builds_the_view_frame_per_the_contract",
        "tests/unit/ui/test_relay_static.py::test_relay_iframe_attributes",
        "tests/unit/core/apps/test_csp.py::test_default_policy_is_pinned",
        "tests/unit/ui/proxy/test_origin_pair.py::test_any_non_loopback_address_disables_apps",
    ),
    "App calling other Servers'": (
        "tests/unit/core/test_invoker_app_scope.py::test_app_scope_denies_without_prompt",
        "tests/integration/test_invoker_app_scope.py::test_out_of_scope_calls_never_reach_a_server",
        "tests/integration/test_invoker_app_scope.py::test_own_app_visible_tool_is_consented_and_called",
        "tests/integration/apps/test_bridge_fixture.py::test_app_hidden_tool_refused",
        f"{E2E_APPS}::test_app_hidden_tool_is_refused_and_audited",
        f"{E2E_APPS}::test_app_button_is_one_consented_audited_invoker_call",
    ),
    "Forged postMessage": (
        f"{E2E_APPS}::test_forged_messages_never_reach_the_invoker",
        "tests/unit/ui/test_relay_static.py::test_relay_checks_identity_and_origin",
        f"{PROXY}::test_page_filters_parent_messages_by_source_and_pinned_origin",
        f"{PROXY}::test_page_filters_view_messages_by_window_identity",
        "tests/unit/core/apps/test_bridge_limits.py::test_malformed_gets_code_and_bridge_keeps_running",
    ),
    "Server content executing on the Host UI origin": (
        "tests/unit/ui/test_relay_static.py::test_relay_never_turns_data_into_markup_or_script",
        "tests/unit/ui/test_relay_static.py::test_no_ui_module_builds_script",
        f"{PROXY}::test_page_never_builds_script_from_message_data",
    ),
    "CSP injection through declared domains": (
        "tests/unit/core/apps/test_csp.py::test_invalid_entries_are_dropped",
        "tests/unit/core/apps/test_csp.py::test_wss_only_in_connect_domains",
        "tests/integration/apps/test_bridge_fixture.py::test_bad_csp_dropped_and_logged",
        f"{E2E_APPS}::test_resource_limit_and_csp_notices",
    ),
    "View flooding consent prompts": (
        "tests/unit/core/apps/test_bridge_limits.py::test_fifth_concurrent_request_refused",
        "tests/unit/core/apps/test_messages.py::test_oversized_messages",
        "tests/unit/core/apps/test_resource.py::test_size_limit_exact",
        "tests/unit/core/apps/test_bridge_methods.py::test_resources_read_limits_and_errors",
    ),
    "View opening `javascript:`": (
        "tests/unit/core/apps/test_bridge_methods.py::test_open_link_not_openable",
        "tests/unit/core/apps/test_bridge_methods.py::test_open_link_confirmed_and_declined",
        "tests/unit/ui/test_app_models.py::test_is_openable_url_length_and_whitespace",
        "tests/unit/ui/test_app_models.py::test_link_confirmation_text_shows_the_full_url",
        "tests/contract/test_reflex_surface.py::test_external_redirect_opens_with_noopener",
        f"{E2E_APPS}::test_open_link_confirmation_and_noopener_tab",
    ),
    "Referrer leak": (
        f"{GUARD}::test_every_guard_response_has_no_referrer_policy",
        f"{GUARD}::test_installed_backend_responses_have_no_referrer_policy",
        "tests/e2e/test_referrer.py::test_open_link_sends_no_referrer",
        "tests/e2e/test_referrer.py::test_url_elicitation_sends_no_referrer",
    ),
    "DNS rebinding / foreign requests to the sandbox proxy": (
        f"{PROXY}::test_wrong_host_header_is_forbidden",
        f"{PROXY}::test_other_paths_are_not_found",
        f"{PROXY}::test_other_methods_are_not_allowed",
        f"{PROXY}::test_bad_view_id_is_a_bad_request",
        f"{PROXY}::test_get_proxy_serves_the_page_with_strict_headers",
        f"{PROXY}::test_page_pins_the_ui_origin_and_never_echoes_the_query",
        f"{PROXY}::test_oversized_head_is_refused_and_closed",
        f"{PROXY}::test_idle_client_is_closed_at_the_read_deadline",
        f"{PROXY}::test_requests_are_never_logged_with_their_content",
        f"{PROXY}::test_the_33rd_connection_gets_503_and_is_closed",
        f"{PROXY}::test_refused_connections_leak_no_task_or_descriptor",
    ),
    "Tab state written to disk": (
        "tests/unit/app/test_reference_app.py::test_rxconfig_keeps_tab_state_in_memory",
        "tests/unit/ui/test_install.py::test_a_state_manager_other_than_memory_refuses_to_start",
        "tests/unit/ui/test_install.py::test_the_mode_is_the_one_reflex_will_create",
    ),
    "MCP App View navigating its own frame": (
        f"{E2E_APPS}::test_a_view_that_navigates_its_frame_is_discarded",
        f"{E2E_APPS}::test_a_forged_view_navigated_from_a_sibling_is_ignored",
        f"{PROXY}::test_page_reports_a_navigated_view_and_stops_relaying_it",
        "tests/unit/core/apps/test_bridge_teardown.py::"
        "test_discard_navigated_sends_nothing_and_cancels_in_flight_calls",
    ),
    "DNS rebinding / CSRF on local backend": (
        f"{GUARD}::test_http_matrix",
        f"{GUARD}::test_websocket_matrix",
        f"{GUARD}::test_duplicate_host_headers_are_refused",
        f"{GUARD}::test_requests_before_start_up_fail_closed",
        f"{GUARD}::test_remote_mode_accepts_only_the_concrete_origin",
        f"{GUARD}::test_installed_guard_covers_the_reflex_backend",
        f"{GUARD}::test_unacceptable_deploy_urls_refuse_to_start",
    ),
    "Server-provided URLs opened by the UI": (
        "tests/unit/ui/test_prompt_cards.py::test_is_openable_url",
        "tests/unit/ui/test_prompt_cards.py::test_non_http_url_is_declined_without_a_redirect",
        "tests/unit/ui/test_prompt_cards.py::test_url_handler_redirects_only_on_accept",
        "tests/unit/core/handlers/test_elicitation.py::test_url_mode_rejects_other_schemes",
        "tests/integration/ui/test_servers_panel.py::test_a_refused_authorization_url_cancels_the_flow",
    ),
    "OAuth code interception": (
        "tests/integration/auth/test_authorize.py::test_authorize_completes_and_stores_tokens",
        "tests/integration/auth/test_authorize.py::test_forged_callback_does_not_end_the_flow",
        "tests/integration/auth/test_authorize.py::test_bad_callback_aborts_before_redemption",
        "tests/unit/core/auth/test_callback_server.py::test_binds_loopback_only_and_serves_one_callback",
        "tests/unit/core/auth/test_callback_server.py::test_rejected_callback_keeps_listening",
        "tests/contract/test_mcp_sdk_auth_surface.py::test_iss_validation_rules",
        "tests/integration/auth/test_resource_binding.py::test_url_change_clears_the_stored_entry",
        "tests/unit/core/auth/test_token_store.py::test_tokens_of_another_issuer_are_dropped_on_read",
        "tests/unit/core/auth/test_token_store.py::test_tokens_are_withheld_before_discovery",
        f"{KEYRING}::test_keyring_delete_failure_signs_out_fail_closed",
        f"{KEYRING}::test_ui_sign_out_with_a_failing_keyring_is_a_fixed_notice",
    ),
    "OAuth secrets in SDK error text": (
        "tests/unit/core/auth/test_sdk_log_hygiene.py::test_sdk_exception_text_is_stripped",
        "tests/unit/core/auth/test_sdk_log_hygiene.py::test_registered_secrets_are_redacted_and_parking_is_silent",
        "tests/contract/test_mcp_sdk_auth_surface.py::test_every_sdk_auth_logger_is_protected",
        "tests/integration/auth/test_authorize.py::test_no_secret_in_logs_errors_or_status",
        f"{REDACTION}::test_no_seeded_secret_appears_anywhere",
    ),
    "Payloads in SDK debug logs": (
        "tests/unit/core/test_logging.py::test_sdk_payload_logs_are_stripped",
        "tests/unit/core/test_logging.py::test_sdk_exception_payloads_are_stripped",
        "tests/contract/test_mcp_sdk_auth_surface.py::test_every_sdk_client_logger_is_protected",
        "tests/unit/core/test_schema.py::test_compile_error_detail_is_redacted",
        f"{REDACTION}::test_no_seeded_secret_appears_anywhere",
    ),
    "Secrets in logs/UI/DB": (
        "tests/unit/core/test_secrets.py::test_registered_value_is_masked",
        "tests/unit/core/test_logging.py::test_secret_never_reaches_records",
        f"{REDACTION}::test_no_seeded_secret_appears_anywhere",
        f"{REDACTION}::test_mcp_app_log_entries_carry_no_view_data",
        f"{REDACTION}::test_phase8_payloads_carry_no_argument_values",
        "tests/security/test_file_modes.py::test_every_file_the_host_writes_is_user_only",
    ),
    "Low-entropy argument recovery": (
        "tests/unit/core/test_audit.py::test_equal_arguments_equal_hash_different_installs_differ",
        "tests/unit/core/test_audit.py::test_raw_arguments_never_reach_the_database",
        "tests/unit/core/test_hmac_key.py::test_key_never_appears_in_repr_or_logs",
    ),
    "Remote `$ref` / schema bombs / ReDoS": (
        "tests/unit/core/test_schema.py::test_remote_ref_is_not_fetched",
        "tests/unit/core/test_schema.py::test_deep_schema_fails_within_bounded_time",
        "tests/unit/core/test_schema.py::test_huge_schema_and_pattern_are_refused",
        "tests/integration/test_schema_redos.py::test_catastrophic_input_pattern_is_unusable_schema",
        "tests/integration/test_schema_redos.py::test_catastrophic_output_pattern_is_protocol_error",
        "tests/integration/test_schema_redos.py::test_hostile_server_does_not_block_the_event_loop",
        "tests/contract/test_mcp_sdk_surface.py::test_call_tool_validates_results_through_overridable_method",
        "tests/architecture/test_schema_regex_rule.py::test_core_schema_never_uses_stdlib_re",
    ),
    "Server-provided `requestState` tampering": (
        "tests/integration/test_mrtr.py::test_request_state_echoed_byte_identical",
        "tests/unit/core/test_mrtr.py::test_rounds_fulfil_every_request_and_echo_state",
    ),
    "Oversized SSE events": (f"{HERE}::test_sse_event_size_limit_is_the_sdk_default",),
    "Compromised or re-pointed CI dependency": (
        f"{HERE}::test_release_path_actions_are_pinned_to_commit_shas",
        f"{HERE}::test_only_the_tag_gated_publish_job_holds_id_token",
    ),
}
"""TD §6.1 vector prefix → tests proving its mitigation."""

NOTES: dict[str, str] = {
    "MCP App View navigating its own frame": (
        "Residual by design (S-3): the data carried by the first navigation is not "
        "prevented in any engine; only its detection and the View's discard are tested."
    ),
    "MCP App escaping to Host origin": (
        "Real Safari is an owner-only manual pass (F8-15); WebKit (WPE) stands in for it in "
        "the E2E (Plan R-8)."
    ),
    "Compromised or re-pointed CI dependency": (
        "Dependabot review, the `pypi` environment's required reviewer and the Trusted "
        "Publisher configuration live on GitHub (owner, F8-07b); only the repository side "
        "is testable here."
    ),
}
"""TD §6.1 vector prefix → what no automated test can cover."""


def td_vectors() -> list[str]:
    """The first-column text of every TD §6.1 table row."""
    text = TD.read_text(encoding="utf-8")
    section = text[text.index("### 6.1 Attack surface") : text.index("### 6.2 Sensitive data")]
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("| ") and not line.startswith("| Vector")
    ]
    return [row.strip().strip("|").split("|")[0].strip() for row in rows]


@cache
def _test_functions(path: str) -> frozenset[str]:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    )


def test_every_attack_vector_has_evidence() -> None:
    """REQ-NFR-010, REQ-NFR-011, REQ-NFR-012 (Art. 5, TD §6.1): every row of the attack
    surface table is matched by exactly one ``EVIDENCE`` key, every key matches a row, and
    every row cites at least one test (``NOTES`` only add residual risk, never replace it)."""
    vectors = td_vectors()
    assert len(vectors) >= 27
    unmatched = []
    for vector in vectors:
        keys = [key for key in EVIDENCE if vector.startswith(key)]
        if len(keys) != 1:
            unmatched.append((vector[:60], keys))
    assert unmatched == []
    for key, tests in EVIDENCE.items():
        assert sum(v.startswith(key) for v in vectors) == 1, key
        assert tests, key
    assert set(NOTES) <= set(EVIDENCE)


def test_every_cited_test_exists() -> None:
    """REQ-NFR-010 (TD §6.1): each ``path::function`` of ``EVIDENCE`` names an existing
    test function (a renamed or deleted test fails the walk-through)."""
    missing = []
    for tests in EVIDENCE.values():
        for ref in tests:
            path, name = ref.split("::")
            if not (REPO / path).is_file() or name not in _test_functions(path):
                missing.append(ref)
    assert missing == []


def test_sign_out_vectors_cite_sign_out_evidence() -> None:
    """REQ-AUTH-007, REQ-NFR-020 (TD §6.1, Phase 8 gap G-15): a vector whose first column
    names a sign-out cites at least one test that exercises a sign-out (its function name
    says ``sign_out`` / ``signs_out``), so widening a row never leaves the new case without
    its own evidence."""
    vectors = [vector for vector in td_vectors() if "sign-out" in vector]
    assert vectors
    for vector in vectors:
        (key,) = [key for key in EVIDENCE if vector.startswith(key)]
        names = [ref.split("::")[1] for ref in EVIDENCE[key]]
        assert any("sign_out" in n or "signs_out" in n for n in names), key


def test_sse_event_size_limit_is_the_sdk_default() -> None:
    """TD §6.1 "Oversized SSE events", Art. 5: the pinned SDK bounds one SSE event (1 MiB
    default, never ``None``) and no Host module overrides ``max_sse_event_size``, so every
    Streamable HTTP Client keeps the bound."""
    default = (
        inspect.signature(streamable_http.streamable_http_client)
        .parameters["max_sse_event_size"]
        .default
    )
    assert default == streamable_http.DEFAULT_MAX_SSE_EVENT_SIZE
    assert isinstance(default, int)
    assert 0 < default <= 1024 * 1024
    overriding = [
        str(p.relative_to(REPO))
        for p in SRC.rglob("*.py")
        if "max_sse_event_size" in p.read_text(encoding="utf-8")
    ]
    assert overriding == []


_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.MULTILINE)


def test_release_path_actions_are_pinned_to_commit_shas() -> None:
    """TD §6.1 "Compromised or re-pointed CI dependency", DD-015: every ``uses:`` in every
    workflow names a full 40-hex commit SHA (a moved tag cannot change what runs)."""
    seen = 0
    loose = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for ref in _USES.findall(workflow.read_text(encoding="utf-8")):
            seen += 1
            if ref.startswith("./"):
                continue  # a local action is versioned with the repository
            if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref):
                loose.append(f"{workflow.name}: {ref}")
    assert seen >= 10
    assert loose == []


def _jobs(text: str) -> dict[str, str]:
    """``jobs:`` of a workflow → each job's text block (two-space job keys)."""
    body = text[text.index("\njobs:") :]
    parts = re.split(r"^  ([\w-]+):\s*$", body, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def test_only_the_tag_gated_publish_job_holds_id_token() -> None:
    """TD §6.1 "Compromised or re-pointed CI dependency", §9.1: no workflow grants
    ``id-token: write`` at the top level; in the release path (``release.yml``) only the
    ``publish`` job does — which runs only for ``v*`` tag pushes, in environment ``pypi``,
    and pins ``uv``. Outside it, only GitHub Pages' ``deploy`` job (environment
    ``github-pages``, required by ``actions/deploy-pages``) holds it; ``ci.yml`` never."""
    allowed = {"release.yml": ["publish"], "pages.yml": ["deploy"]}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        top = text[: text.index("\njobs:")]
        assert "id-token: write" not in top, workflow.name
        holders = [name for name, block in _jobs(text).items() if "id-token: write" in block]
        assert holders == allowed.get(workflow.name, []), workflow.name
    deploy = _jobs((WORKFLOWS / "pages.yml").read_text(encoding="utf-8"))["deploy"]
    assert re.search(r"environment:\s*\n\s+name:\s*github-pages", deploy)
    publish = _jobs((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))["publish"]
    assert "startsWith(github.ref, 'refs/tags/v')" in publish
    assert re.search(r"environment:\s*\n\s+name:\s*pypi|environment:\s*pypi", publish)
    assert re.search(r"version:\s*\"?\d+\.\d+\.\d+", publish), "uv pinned in publish"
