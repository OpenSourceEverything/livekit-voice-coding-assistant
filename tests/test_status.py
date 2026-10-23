"""The single local view never exposes credentials or mistakes limits for credit."""

import json

from tools.status import format_status, local_status


def test_first_run_is_useful_and_does_not_invent_usage(tmp_path):
    report = local_status(tmp_path, environment={})
    text = format_status(report)
    assert "No recorded sessions yet" in text
    assert "Remaining monthly account credit: unknown" in text
    assert "Invoice, plan allowances, and phone rental: check LiveKit Billing." in text
    assert "PR creation / phone approval / deployment: not implemented" in text
    assert not report["credentials_configured"]
    assert not report["phone_proposals_enabled"]
    assert report["interruption_mode"] == "vad"
    assert report["usage"]["remaining_monthly_credit_usd"] is None


def test_adaptive_mode_is_visible_without_echoing_arbitrary_config(tmp_path):
    report = local_status(tmp_path, environment={"ADAPTIVE_INTERRUPTION": "true"})
    assert "Configured interruption mode: adaptive" in format_status(report)
    report = local_status(tmp_path, environment={"ADAPTIVE_INTERRUPTION": "PRIVATE"})
    assert "PRIVATE" not in format_status(report)
    assert report["interruption_mode"] == "vad"


def test_private_config_only_exposes_selected_status_and_direct_link(tmp_path):
    (tmp_path / ".env.local").write_text(
        "LIVEKIT_URL=wss://private.invalid\n"
        "LIVEKIT_API_KEY=secret-key\n"
        "LIVEKIT_API_SECRET=secret-value\n"
        "PHONE_ACCESS_PIN=654321\n"
        "LIVEKIT_PROJECT_ID=p_ABC123\n"
        "ENABLE_PHONE_PROPOSALS=true\n"
    )
    report = local_status(tmp_path, environment={})
    output = json.dumps(report) + format_status(report)
    for secret in ("private.invalid", "secret-key", "secret-value", "654321"):
        assert secret not in output
    assert report["credentials_configured"]
    assert report["phone_proposals_enabled"]
    assert report["usage"]["account_billing_url"] == (
        "https://cloud.livekit.io/projects/p_ABC123/billing"
    )


def test_cloud_mode_and_explicit_disabled_proposals_cannot_appear_enabled(tmp_path):
    base = {"PHONE_ACCESS_PIN": "654321", "ENABLE_PHONE_PROPOSALS": "true"}
    for option in ({"COPILOT_MODE": "cloud-demo"}, {"ENABLE_PROPOSALS": "false"}):
        assert not local_status(tmp_path, environment=base | option)[
            "phone_proposals_enabled"
        ]


def test_invalid_project_link_and_invalid_pin_are_not_echoed(tmp_path):
    report = local_status(
        tmp_path,
        environment={
            "LIVEKIT_PROJECT_ID": "p_../../private-secret",
            "PHONE_ACCESS_PIN": "wrong-secret",
        },
    )
    assert "account_billing_url" not in report["usage"]
    assert not report["phone_pin_configured"]
    assert "secret" not in json.dumps(report)


def test_unreadable_usage_does_not_look_like_zero_spend(tmp_path):
    (tmp_path / "bad-ledger").mkdir()
    report = local_status(tmp_path, environment={"USAGE_PATH": "bad-ledger"})
    assert report["usage_read_failed"]
    assert "Usage ledger unreadable" in format_status(report)
    assert "No recorded sessions" not in format_status(report)


def test_workspace_control_characters_are_escaped_in_terminal(tmp_path):
    report = local_status(tmp_path, environment={"REPO_ROOT": "line\n\x1b[31m"})
    text = format_status(report)
    assert "\x1b" not in text
    assert "line\\n\\u001b" in text
