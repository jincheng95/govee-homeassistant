"""Public-repo hygiene gate: no real identifiers in tracked files.

Scans every git-tracked text file — including this one — for device ids, BLE
MACs, AWS IoT topics, globally-routable IP addresses and account-secret fields,
and fails if any key material is tracked at all. Fixtures must be synthetic;
captures and provenance stay out of the public tree.

The allowlists are explicit full values, never prefixes: widening one is a
review decision, not a way to make the gate quiet. Lines this file needs to
carry that would otherwise trip it sit between the exemption markers below.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Lines between these markers are skipped when this file scans itself: they are
# the allowlists and the deliberate samples, which are the gate, not a leak.
EXEMPT_BEGIN = "hygiene: exempt-begin"
EXEMPT_END = "hygiene: exempt-end"

BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".p12", ".pem", ".pyc", ".zip", ".woff", ".woff2", ".pdf"}
)

# Key material must not be tracked at all — the file's presence is the failure,
# whatever is in it.
KEY_MATERIAL_SUFFIXES = frozenset({".pem", ".p12", ".pfx", ".key", ".crt", ".cer", ".der", ".jks"})

# --- device ids and MACs ----------------------------------------------------

# Eight octets or more: the extended 16-octet form is one id, not two.
DEVICE_ID_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){7,}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")

# Exactly six octets: a BLE/LAN MAC. The trailing look-ahead keeps this from
# matching the first six octets of a device id.
MAC_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")

# The dash-separated spellings of both, which the colon patterns cannot see.
DASHED_RE = re.compile(r"(?<![0-9A-Fa-f-])(?:[0-9A-Fa-f]{2}-){5,}[0-9A-Fa-f]{2}(?![0-9A-Fa-f-])")

# hygiene: exempt-begin
ALLOWED_DEVICE_IDS = frozenset(
    {
        "00:00:00:00:00:00:00:00",
        "00:11:22:33:44:55:66:77",
        "00:11:AA:BB:CC:DD:EE:FF",
        "00:22:11:22:33:44:55:66",
        "11:22:33:44:55:66:00:22",
        "11:22:33:44:55:66:00:AA",
        "11:22:33:44:55:66:2A:22",
        "11:22:33:44:55:66:50:44",
        "11:22:33:44:55:66:50:46",
        "11:22:33:44:55:66:51:00",
        "11:22:33:44:55:66:53:10",
        "11:22:33:44:55:66:60:1F",
        "11:22:33:44:55:66:61:8E",
        "11:22:33:44:55:66:70:00",
        "11:22:33:44:55:66:70:01",
        "11:22:33:44:55:66:77:88",
        "11:22:33:44:55:66:77:98",
        "11:22:33:44:55:66:77:99",
        "99:99:99:99:99:99:99:99",
        "AA:11:22:33:44:55:66:77",
        "AA:AA:AA:AA:AA:AA:AA:AA",
        "AA:BB:77:88:99:AA:BB:CC",
        "AA:BB:AA:BB:CC:11:22:33",
        "AA:BB:CC:DD:EE:FF:00:01",
        "AA:BB:CC:DD:EE:FF:00:02",
        "AA:BB:CC:DD:EE:FF:00:03",
        "AA:BB:CC:DD:EE:FF:00:11",
        "AA:BB:CC:DD:EE:FF:00:22",
        "AA:BB:CC:DD:EE:FF:00:33",
        "AA:BB:CC:DD:EE:FF:00:44",
        "AA:BB:CC:DD:EE:FF:00:55",
        "AA:BB:CC:DD:EE:FF:00:66",
        "AA:BB:CC:DD:EE:FF:00:77",
        "AA:BB:CC:DD:EE:FF:00:88",
        "AA:BB:CC:DD:EE:FF:00:98",
        "AA:BB:CC:DD:EE:FF:00:99",
        "AA:BB:CC:DD:EE:FF:11:22",
        "AA:BB:CC:DD:EE:FF:11:23",
        "AA:BB:CC:DD:EE:FF:13:10",
        "AA:BB:CC:DD:EE:FF:13:70",
        "AA:BB:CC:DD:EE:FF:1C:42",
        "AA:BB:CC:DD:EE:FF:41:02",
        "AA:BB:CC:DD:EE:FF:50:54",
        "AA:BB:CC:DD:EE:FF:50:89",
        "AA:BB:CC:DD:EE:FF:51:06",
        "AA:BB:CC:DD:EE:FF:51:10",
        "AA:BB:CC:DD:EE:FF:51:11",
        "AA:BB:CC:DD:EE:FF:51:27",
        "AA:BB:CC:DD:EE:FF:51:40",
        "AA:BB:CC:DD:EE:FF:60:01",
        "AA:BB:CC:DD:EE:FF:60:46",
        "AA:BB:CC:DD:EE:FF:60:76",
        "AA:BB:CC:DD:EE:FF:60:B0",
        "AA:BB:CC:DD:EE:FF:60:B1",
        "AA:BB:CC:DD:EE:FF:60:B2",
        "AA:BB:CC:DD:EE:FF:60:B3",
        "AA:BB:CC:DD:EE:FF:61:99",
        "AA:BB:CC:DD:EE:FF:70:01",
        "AA:BB:CC:DD:EE:FF:71:06",
        "AA:BB:CC:DD:EE:FF:71:07",
        "AA:BB:CC:DD:EE:FF:71:24",
        "AA:BB:CC:DD:EE:FF:71:50",
        "AA:BB:CC:DD:EE:FF:71:52",
        "AA:BB:CC:DD:EE:FF:71:70",
        "AA:BB:CC:DD:EE:FF:99:99",
        "AA:BB:CC:DD:EE:FF:AB:FA",
        "AA:BB:CC:DD:EE:FF:F0:B2",
        "AA:BB:DD:EE:FF:44:55:66",
        "BB:11:22:33:44:55:66:77",
        "BB:BB:BB:BB:BB:BB:BB:BB",
        "BB:CC:DD:EE:FF:00:11:22",
        "CC:CC:CC:CC:CC:CC:CC:CC",
        "FF:FF:FF:FF:FF:FF:FF:FF",
    }
)

# The synthetic six-octet MACs in use. A Govee BLE MAC is the device id's last
# six octets, so these are the tails of the ids above.
ALLOWED_MACS = frozenset(
    {
        "00:00:00:00:00:00",
        "11:22:33:44:55:66",
        "77:88:99:AA:BB:CC",
        "99:88:77:66:55:44",
        "AA:BB:CC:11:22:33",
        "AA:BB:CC:DD:EE:FF",
        "CC:DD:EE:FF:00:11",
        "DD:EE:FF:44:55:66",
    }
)
# hygiene: exempt-end

# --- AWS IoT topics ---------------------------------------------------------

# A real topic is `GA/`/`GD/` + a 32-hex id; the docs' placeholders are short
# or bracketed.
IOT_TOPIC_RE = re.compile(r"\bG[AD]/[0-9A-Fa-f]{16,}")

# --- IP addresses -----------------------------------------------------------

IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")

# --- account secrets --------------------------------------------------------

SECRET_FIELD_RE = re.compile(r"[\"']?(secretCode|accountTopic)[\"']?\s*[:=]\s*[\"']([^\"']*)[\"']")

# Exact, lower-cased stand-ins. A substring heuristic used to accept anything
# containing "str", which matched real values by accident.
PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "...",
        "<redacted>",
        "<secret>",
        "[redacted]",
        "dummy",
        "example",
        "placeholder",
        "redacted",
        "str",
        "xxx",
        "your-secret",
    }
)


def _tracked_files() -> list[str]:
    """Every git-tracked path, unfiltered."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _tracked_text_files() -> list[str]:
    """Every git-tracked path this gate can meaningfully read (this file included)."""
    return [p for p in _tracked_files() if Path(p).suffix.lower() not in BINARY_SUFFIXES]


def _read(path: str) -> str | None:
    """File text, or None when it is not decodable as UTF-8 (treated as binary)."""
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, OSError):
        return None


def _scannable_lines(text: str) -> list[tuple[int, str]]:
    """Numbered lines, dropping anything between the exemption markers."""
    lines: list[tuple[int, str]] = []
    exempt = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if EXEMPT_BEGIN in line:
            exempt = True
            continue
        if EXEMPT_END in line:
            exempt = False
            continue
        if not exempt:
            lines.append((lineno, line))
    return lines


def _scan(finder) -> list[str]:
    """Apply ``finder`` to every scannable line of every tracked text file.

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
        for lineno, line in _scannable_lines(text):
            for value in finder(line):
                hits.append(f"{path}:{lineno} {value}")
    return hits


def _unlisted_hardware_ids(line: str) -> list[str]:
    """Device ids and MACs in ``line`` that are not allowlisted placeholders."""
    found = []
    for match in DEVICE_ID_RE.findall(line):
        # An id is judged by its first eight octets; any extended tail is
        # address structure, not identity.
        if ":".join(match.upper().split(":")[:8]) not in ALLOWED_DEVICE_IDS:
            found.append(match)
    for match in MAC_RE.findall(line):
        if match.upper() not in ALLOWED_MACS:
            found.append(match)
    for match in DASHED_RE.findall(line):
        octets = match.upper().split("-")
        colonised = ":".join(octets)
        allowed = ALLOWED_MACS if len(octets) == 6 else ALLOWED_DEVICE_IDS
        if ":".join(colonised.split(":")[:8]) not in allowed:
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
    """Whether a secret field's value is one of the listed stand-ins."""
    return value.strip().lower() in PLACEHOLDER_VALUES


def _secret_values(line: str) -> list[str]:
    """Account-secret fields in ``line`` carrying a non-placeholder value."""
    return [f"{field}={value}" for field, value in SECRET_FIELD_RE.findall(line) if not _is_placeholder(value)]


class TestNoRealIdentifiers:
    """Nothing tracked in this repo may identify a real account or device."""

    def test_no_unlisted_hardware_ids(self):
        hits = _scan(_unlisted_hardware_ids)

        assert hits == [], "Non-allowlisted device ids / MACs:\n" + "\n".join(hits)

    def test_no_aws_iot_topics(self):
        hits = _scan(IOT_TOPIC_RE.findall)

        assert hits == [], "AWS IoT account/device topics:\n" + "\n".join(hits)

    def test_no_routable_ip_addresses(self):
        hits = _scan(_routable_ips)

        assert hits == [], "Globally-routable IP addresses:\n" + "\n".join(hits)

    def test_no_account_secret_values(self):
        hits = _scan(_secret_values)

        assert hits == [], "Account secret fields carrying real values:\n" + "\n".join(hits)

    def test_no_key_material_is_tracked(self):
        """A certificate or private key in the tree is a leak by its presence."""
        keys = [p for p in _tracked_files() if Path(p).suffix.lower() in KEY_MATERIAL_SUFFIXES]

        assert keys == [], "Key material tracked in the public tree:\n" + "\n".join(keys)

    def test_no_research_dump_directory(self):
        """Raw research dumps are provenance, and provenance is not public."""
        dumps = [p for p in _tracked_text_files() if p.startswith("docs/_research/")]

        assert dumps == []


class TestTheGateScansItself:
    """The gate is a tracked file too, and used to be the one file exempt."""

    def test_this_file_is_in_the_scan_set(self):
        assert "tests/test_repo_hygiene.py" in _tracked_text_files()

    def test_the_exemption_is_two_bounded_blocks(self):
        """Only the allowlists and the deliberate samples are exempt."""
        source = _read("tests/test_repo_hygiene.py")
        lines = source.splitlines()

        opens = [n for n, line in enumerate(lines) if EXEMPT_BEGIN in line and "=" not in line]
        closes = [n for n, line in enumerate(lines) if EXEMPT_END in line and "=" not in line]
        assert len(opens) == len(closes) == 2
        assert all(close > open_ for open_, close in zip(opens, closes))

    def test_the_gate_reads_its_own_unexempted_lines(self):
        """The scan really covers this file's body, not just its name."""
        source = _read("tests/test_repo_hygiene.py")

        scanned = {line for _lineno, line in _scannable_lines(source)}

        assert any("def _unlisted_hardware_ids" in line for line in scanned)


class TestTheGateFires:
    """A gate that cannot fail is not a gate."""

    # hygiene: exempt-begin
    @pytest.mark.parametrize(
        ("finder", "sample"),
        [
            (_unlisted_hardware_ids, "device = 'A1:B2:C3:D4:E5:F6:07:08'"),
            (_unlisted_hardware_ids, "device = 'A1:B2:C3:D4:E5:F6:07:08:FF:FF:00:33:FF:FF:00:4C'"),
            (_unlisted_hardware_ids, "mac = 'A1:B2:C3:D4:E5:F6'"),
            (_unlisted_hardware_ids, "mac = 'A1-B2-C3-D4-E5-F6'"),
            (_unlisted_hardware_ids, "device = 'A1-B2-C3-D4-E5-F6-07-08'"),
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
            (_unlisted_hardware_ids, "device = 'AA:BB:CC:DD:EE:FF:00:11'"),
            (_unlisted_hardware_ids, "mac = '00:00:00:00:00:00'"),
            (IOT_TOPIC_RE.findall, "topic: GA/<32-hex account topic>"),
            (_routable_ips, "host = '10.20.0.51'"),
            (_routable_ips, "host = '192.0.2.205'"),
            (_secret_values, '"secretCode": "[REDACTED]"'),
        ],
    )
    def test_a_placeholder_is_not_caught(self, finder, sample: str):
        assert not finder(sample)

    # hygiene: exempt-end
