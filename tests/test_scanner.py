"""Tests for secret scanner."""

import pytest
from team_memory.services.scanner import scan_for_secrets, scan_directory, rule_id_to_label


class TestScanForSecrets:
    """Test individual content scanning."""

    def test_no_secrets(self):
        result = scan_for_secrets("This is normal text about team conventions.")
        assert result == []

    def test_aws_access_key(self):
        result = scan_for_secrets("AWS key: AKIAIOSFODNN7EXAMPLE")
        assert len(result) >= 1
        assert any(r.rule_id == "aws-access-token" for r in result)

    def test_github_pat(self):
        result = scan_for_secrets("My token: ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t")
        assert len(result) >= 1
        assert any(r.rule_id == "github-pat" for r in result)

    def test_openai_key(self):
        result = scan_for_secrets(
            "OPENAI_KEY=sk-proj-" + "a" * 58 + "T3BlbkFJ" + "b" * 58
        )
        assert len(result) >= 1
        assert any(r.rule_id == "openai-api-key" for r in result)

    def test_anthropic_key(self):
        pfx = "-".join(["sk", "ant", "api"])
        key = f"{pfx}03-{'x' * 93}AA"
        result = scan_for_secrets(key)
        assert len(result) >= 1
        assert any(r.rule_id == "anthropic-api-key" for r in result)

    def test_gcp_key(self):
        result = scan_for_secrets("AIza" + "x" * 35)
        assert len(result) >= 1
        assert any(r.rule_id == "gcp-api-key" for r in result)

    def test_slack_bot_token(self):
        result = scan_for_secrets("xoxb-1234567890-1234567890123-abc123def456")
        # May also match slack-user-token or slack-app-token patterns
        assert len(result) >= 1

    def test_private_key_pem(self):
        content = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj
MzEfYyjiWA4R4/M2bS1+fVtJGQ==
-----END PRIVATE KEY-----"""
        result = scan_for_secrets(content)
        assert len(result) >= 1
        assert any(r.rule_id == "private-key" for r in result)

    def test_stripe_key(self):
        result = scan_for_secrets("sk_live_abcdefghijklmnop12345")
        assert len(result) >= 1
        assert any(r.rule_id == "stripe-access-token" for r in result)

    def test_gitlab_pat(self):
        result = scan_for_secrets("glpat-abcdefghijklmnopqrst")
        assert len(result) >= 1
        assert any(r.rule_id == "gitlab-pat" for r in result)

    def test_status_code(self):
        """Detected secrets should appear in result."""
        result = scan_for_secrets("My GitHub token is ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t")
        assert len(result) == 1
        assert result[0].rule_id == "github-pat"
        assert "GitHub" in result[0].label
        assert "PAT" in result[0].label


class TestRuleIdToLabel:
    def test_github_pat(self):
        assert rule_id_to_label("github-pat") == "GitHub PAT"

    def test_aws_access_token(self):
        assert rule_id_to_label("aws-access-token") == "AWS Access Token"

    def test_openai_api_key(self):
        assert rule_id_to_label("openai-api-key") == "OpenAI API Key"

    def test_gcp_api_key(self):
        assert rule_id_to_label("gcp-api-key") == "GCP API Key"
