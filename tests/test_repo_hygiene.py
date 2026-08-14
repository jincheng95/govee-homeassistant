"""Public-repo hygiene gate: no real identifiers in tracked files.

Scans every git-tracked text file for device ids, AWS IoT account/device topics,
globally-routable IP addresses and account-secret fields. Fixtures must be
synthetic; captures and provenance stay out of the public tree.

The device-id allowlist is explicit and small on purpose — widening it is a
review decision, not a way to make the gate quiet.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# This file necessarily contains the patterns and the samples it looks for.
SELF = "tests/test_repo_hygiene.py"

BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".p12", ".pem", ".pyc", ".zip", ".woff", ".woff2", ".pdf"}
)

# --- device ids -------------------------------------------------------------

# Eight octets or more: the extended 16-octet form is one id, not two.
DEVICE_ID_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){7,}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")

# An id is judged by its first eight octets — that is the Govee device id; any
# extended tail is address structure, not identity.
ALLOWED_ID_PREFIXES = (
    "AA:BB:CC:DD:EE:FF",
    "11:22:33:44:55:66",
)

ALLOWED_DEVICE_IDS = frozenset(
    {
        "00:00:00:00:00:00:00:00",
        "00:11:22:33:44:55:66:77",
        "00:11:AA:BB:CC:DD:EE:FF",
        "00:22:11:22:33:44:55:66",
        "11:22:33:44:55:66:77:88",
        "99:99:99:99:99:99:99:99",
        "AA:11:22:33:44:55:66:77",
        "AA:AA:AA:AA:AA:AA:AA:AA",
        "AA:BB:77:88:99:AA:BB:CC",
        "AA:BB:AA:BB:CC:11:22:33",
        "AA:BB:DD:EE:FF:44:55:66",
        "BB:11:22:33:44:55:66:77",
        "BB:BB:BB:BB:BB:BB:BB:BB",
        "BB:CC:DD:EE:FF:00:11:22",
        "CC:CC:CC:CC:CC:CC:CC:CC",
        "FF:FF:FF:FF:FF:FF:FF:FF",
    }
)

# --- AWS IoT topics ---------------------------------------------------------

# A real topic is `GA/`/`GD/` + a 32-hex id; the docs' placeholders are short
# or bracketed.
IOT_TOPIC_RE = re.compile(r"\bG[AD]/[0-9A-Fa-f]{16,}")

# --- IP addresses -----------------------------------------------------------

IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")

# --- account secrets --------------------------------------------------------

SECRET_FIELD_RE = re.compile(r"[\"']?(secretCode|accountTopic)[\"']?\s*[:=]\s*[\"']([^\"']*)[\"']")

PLACEHOLDER_MARKERS = ("redact", "placeholder", "example", "xxx", "...", "<", "your", "dummy", "str")


def _tracked_text_files() -> list[str]:
    """Every git-tracked path this gate can meaningfully read."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    paths = [p for p in out.split("\0") if p]
    return [p for p in paths if Path(p).suffix.lower() not in BINARY_SUFFIXES and p != SELF]


def _read(path: str) -> str | None:
    """File text, or None when it is not decodable as UTF-8 (treated as binary)."""
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, OSError):
        return None


def _scan(finder) -> list[str]:
    """Apply ``finder`` to every line of every tracked text file.

    Args:
        finder: Takes one line, returns the offending values in it.

    Returns:
        ``path:line value`` for every hit, in scan order.
    """
    hits: list[str] = []
    for path in _tracked_text_files():
        text = _read(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for value in finder(line):
                hits.append(f"{path}:{lineno} {value}")
    return hits


def _unlisted_device_ids(line: str) -> list[str]:
    """Device ids in ``line`` that are not recognisable placeholders."""
    found = []
    for match in DEVICE_ID_RE.findall(line):
        head = ":".join(match.upper().split(":")[:8])
        if head not in ALLOWED_DEVICE_IDS and not head.startswith(ALLOWED_ID_PREFIXES):
            found.append(match)
    return found


def _routable_ips(line: str) -> list[str]:
    """Globally-routable IPv4 addresses in ``line``.

    ``is_global`` carries the rule: it already excludes RFC 1918, loopback,
    link-local and the RFC 5737 documentation ranges. Multicast is excluded
    separately — SSDP's 239.255.255.250 is a protocol constant, not a host.

    Private space is allowed rather than forced into the documentation ranges:
    the LAN tests need several distinct subnets to express subnet-mismatch
    cases, which three /24s cannot represent. Only a publicly routable address,
    which identifies a real host, is a violation.
    """
    found = []
    for text in IPV4_RE.findall(line):
        try:
            address = ipaddress.IPv4Address(text)
        except ValueError:  # a version string or similar, not an address
            continue
        if address.is_global and not address.is_multicast:
            found.append(text)
    return found


def _is_placeholder(value: str) -> bool:
    """Whether a secret field's value is a stand-in rather than a real one."""
    lowered = value.lower()
    return not value.strip() or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _secret_values(line: str) -> list[str]:
    """Account-secret fields in ``line`` carrying a non-placeholder value."""
    return [f"{field}={value}" for field, value in SECRET_FIELD_RE.findall(line) if not _is_placeholder(value)]


class TestNoRealIdentifiers:
    """Nothing tracked in this repo may identify a real account or device."""

    def test_no_unlisted_device_ids(self):
        hits = _scan(_unlisted_device_ids)

        assert hits == [], "Non-allowlisted device ids:\n" + "\n".join(hits)

    def test_no_aws_iot_topics(self):
        hits = _scan(IOT_TOPIC_RE.findall)

        assert hits == [], "AWS IoT account/device topics:\n" + "\n".join(hits)

    def test_no_routable_ip_addresses(self):
        hits = _scan(_routable_ips)

        assert hits == [], "Globally-routable IP addresses:\n" + "\n".join(hits)

    def test_no_account_secret_values(self):
        hits = _scan(_secret_values)

        assert hits == [], "Account secret fields carrying real values:\n" + "\n".join(hits)

    def test_no_research_dump_directory(self):
        """Raw research dumps are provenance, and provenance is not public."""
        dumps = [p for p in _tracked_text_files() if p.startswith("docs/_research/")]

        assert dumps == []


class TestTheGateFires:
    """A gate that cannot fail is not a gate."""

    @pytest.mark.parametrize(
        ("finder", "sample"),
        [
            (_unlisted_device_ids, "device = 'A1:B2:C3:D4:E5:F6:07:08'"),
            (_unlisted_device_ids, "device = 'A1:B2:C3:D4:E5:F6:07:08:FF:FF:00:33:FF:FF:00:4C'"),
            (IOT_TOPIC_RE.findall, "topic: GA/deadbeefdeadbeefdeadbeefdeadbeef"),
            (_routable_ips, "endpoint = '203.0.114.9'"),
            (_secret_values, '"secretCode": "9f2c41ab7d6e"'),
        ],
    )
    def test_a_reintroduced_value_is_caught(self, finder, sample: str):
        assert finder(sample)

    @pytest.mark.parametrize(
        ("finder", "sample"),
        [
            (_unlisted_device_ids, "device = 'AA:BB:CC:DD:EE:FF:00:11'"),
            (IOT_TOPIC_RE.findall, "topic: GA/<32-hex account topic>"),
            (_routable_ips, "host = '10.20.0.51'"),
            (_routable_ips, "host = '192.0.2.205'"),
            (_secret_values, '"secretCode": "[REDACTED]"'),
        ],
    )
    def test_a_placeholder_is_not_caught(self, finder, sample: str):
        assert not finder(sample)
