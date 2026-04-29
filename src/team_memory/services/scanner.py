"""Secret scanner for team memory — ported from ccb secretScanner.ts.

Scans .md content for credentials before upload. Uses a curated subset of
high-confidence rules from gitleaks (MIT license). Only rules with distinctive
prefixes that have near-zero false-positive rates are included.
"""

import re
from dataclasses import dataclass

# ─── Rule definitions ─────────────────────────────────────────────────

# Anthropic API key prefix, assembled at module level so the literal byte
# sequence isn't present as a contiguous string.
ANT_KEY_PFX = "-".join(["sk", "ant", "api"])

_SECRET_RULES: list[tuple[str, str, int]] = [
    # Fields: (rule_id, pattern_string, flags_int)
    # flags: 0 = default, re.IGNORECASE = 2

    # -- Cloud providers --
    ("aws-access-token", r"\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\b", 0),
    ("gcp-api-key", r"\b(AIza[\w-]{35})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("azure-ad-client-secret", r'(?:^|[\\\'\"\x60\s>=:(,)])'
     r'([a-zA-Z0-9_~.]{3}\dQ~[a-zA-Z0-9_~.-]{31,34})'
     r'(?:$|[\\\'\"\x60\s<),])', 0),
    ("digitalocean-pat", r"\b(dop_v1_[a-f0-9]{64})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("digitalocean-access-token", r"\b(doo_v1_[a-f0-9]{64})(?:[\x60'\"\s;]|\\[nr]|$)", 0),

    # -- AI APIs --
    ("anthropic-api-key",
     rf"\b({ANT_KEY_PFX}03-[a-zA-Z0-9_\-]{{93}}AA)(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("anthropic-admin-api-key",
     r"\b(sk-ant-admin01-[a-zA-Z0-9_\-]{93}AA)(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("openai-api-key",
     r"\b(sk-(?:proj|svcacct|admin)-(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})"
     r"T3BlbkFJ(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})"
     r"\b|sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20})"
     r"(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("huggingface-access-token",
     r"\b(hf_[a-zA-Z]{34})(?:[\x60'\"\s;]|\\[nr]|$)", 0),

    # -- Version control --
    ("github-pat", r"ghp_[0-9a-zA-Z]{36}", 0),
    ("github-fine-grained-pat", r"github_pat_\w{82}", 0),
    ("github-app-token", r"(?:ghu|ghs)_[0-9a-zA-Z]{36}", 0),
    ("github-oauth", r"gho_[0-9a-zA-Z]{36}", 0),
    ("github-refresh-token", r"ghr_[0-9a-zA-Z]{36}", 0),
    ("gitlab-pat", r"glpat-[\w-]{20}", 0),
    ("gitlab-deploy-token", r"gldt-[0-9a-zA-Z_\-]{20}", 0),

    # -- Communication --
    ("slack-bot-token", r"xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*", 0),
    ("slack-user-token", r"xox[pe](?:-[0-9]{10,13}){3}-[a-zA-Z0-9-]{28,34}", 0),
    ("slack-app-token", r"xapp-\d-[A-Z0-9]+-\d+-[a-z0-9]+", re.IGNORECASE),
    ("twilio-api-key", r"SK[0-9a-fA-F]{32}", 0),
    ("sendgrid-api-token",
     r"\b(SG\.[a-zA-Z0-9=_\-.]{66})(?:[\x60'\"\s;]|\\[nr]|$)", 0),

    # -- Dev tooling --
    ("npm-access-token", r"\b(npm_[a-zA-Z0-9]{36})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("pypi-upload-token", r"pypi-AgEIcHlwaS5vcmc[\w-]{50,1000}", 0),
    ("databricks-api-token", r"\b(dapi[a-f0-9]{32}(?:-\d)?)(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("hashicorp-tf-api-token",
     r"[a-zA-Z0-9]{14}\.atlasv1\.[a-zA-Z0-9\-_=]{60,70}", 0),
    ("pulumi-api-token", r"\b(pul-[a-f0-9]{40})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("postman-api-token",
     r"\b(PMAK-[a-fA-F0-9]{24}-[a-fA-F0-9]{34})(?:[\x60'\"\s;]|\\[nr]|$)", 0),

    # -- Observability --
    ("grafana-api-key",
     r"\b(eyJrIjoi[A-Za-z0-9+/]{70,400}={0,3})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("grafana-cloud-api-token",
     r"\b(glc_[A-Za-z0-9+/]{32,400}={0,3})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("grafana-service-account-token",
     r"\b(glsa_[A-Za-z0-9]{32}_[A-Fa-f0-9]{8})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("sentry-user-token", r"\b(sntryu_[a-f0-9]{64})(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("sentry-org-token",
     r"\bsntrys_eyJpYXQiO[a-zA-Z0-9+/]{10,200}"
     r"(?:LCJyZWdpb25fdXJs|InJlZ2lvbl91cmwi|cmVnaW9uX3VybCI6)"
     r"[a-zA-Z0-9+/]{10,200}={0,2}_[a-zA-Z0-9+/]{43}", 0),

    # -- Payment / commerce --
    ("stripe-access-token",
     r"\b((?:sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99})"
     r"(?:[\x60'\"\s;]|\\[nr]|$)", 0),
    ("shopify-access-token", r"shpat_[a-fA-F0-9]{32}", 0),
    ("shopify-shared-secret", r"shpss_[a-fA-F0-9]{32}", 0),

    # -- Crypto --
    ("private-key",
     r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----"
     r"[\s\S-]{64,}?-----END[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----",
     re.IGNORECASE),
]

# ─── Compiled rules (lazy) ────────────────────────────────────────────

_compiled: list[tuple[str, re.Pattern[str]]] | None = None


def _get_compiled() -> list[tuple[str, re.Pattern[str]]]:
    global _compiled
    if _compiled is None:
        _compiled = [(rid, re.compile(pat, flags=f)) for rid, pat, f in _SECRET_RULES]
    return _compiled


# ─── Label helpers ─────────────────────────────────────────────────────

_SPECIAL_CASE: dict[str, str] = {
    "aws": "AWS", "gcp": "GCP", "api": "API", "pat": "PAT",
    "ad": "AD", "tf": "TF", "oauth": "OAuth", "npm": "NPM",
    "pypi": "PyPI", "jwt": "JWT", "github": "GitHub", "gitlab": "GitLab",
    "openai": "OpenAI", "digitalocean": "DigitalOcean",
    "huggingface": "HuggingFace", "hashicorp": "HashiCorp",
    "sendgrid": "SendGrid",
}


def rule_id_to_label(rule_id: str) -> str:
    """Convert a gitleaks rule ID to a human-readable label."""
    parts = rule_id.split("-")
    return " ".join(_SPECIAL_CASE.get(p, p.capitalize()) for p in parts)


# ─── Public API ────────────────────────────────────────────────────────

@dataclass
class SecretMatch:
    rule_id: str
    label: str


def scan_for_secrets(content: str) -> list[SecretMatch]:
    """Scan a string for potential secrets. Returns one match per rule fired."""
    matches: list[SecretMatch] = []
    seen: set[str] = set()
    for rule_id, pattern in _get_compiled():
        if rule_id in seen:
            continue
        if pattern.search(content):
            seen.add(rule_id)
            matches.append(SecretMatch(rule_id=rule_id, label=rule_id_to_label(rule_id)))
    return matches


def scan_file(path: str) -> list[SecretMatch]:
    """Scan a file for secrets. Returns matches found."""
    try:
        with open(path) as f:
            return scan_for_secrets(f.read())
    except OSError:
        return []


def scan_directory(dir_path: str) -> dict[str, list[SecretMatch]]:
    """Scan all .md files in a directory recursively.

    Returns {relative_path: [matches]}, only including files with matches.
    """
    from pathlib import Path

    results: dict[str, list[SecretMatch]] = {}
    base = Path(dir_path)
    if not base.is_dir():
        return results

    for md_file in sorted(base.rglob("*.md")):
        if ".git" in md_file.parts:
            continue
        matches = scan_file(str(md_file))
        if matches:
            results[str(md_file.relative_to(base))] = matches
    return results
