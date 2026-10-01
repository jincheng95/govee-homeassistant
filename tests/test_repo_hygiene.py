"""Public-repo hygiene gate: no real identifiers in tracked files.

Scans every git-tracked text file — including this one — for device ids, BLE
MACs, AWS IoT topics, globally-routable IP addresses and account-secret fields,
and fails if any key material is tracked at all. Fixtures must be synthetic;
captures and provenance stay out of the public tree.

The allowlists are explicit full values, never prefixes: widening one is a
review decision, not a way to make the gate quiet. Lines this file needs to
carry that would otherwise trip it sit between the exemption markers below.

Identifiers that upstream ships are upstream-owned and exempt. The committed
baseline (tests/hygiene_upstream_baseline.json) holds the SHA-256 of every
value these finders match in upstream's tree at one commit; a hit whose hash
is in its category's set passes. After each upstream sync, regenerate it with
``python tests/tools/refresh_hygiene_baseline.py upstream/main``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

BASELINE_PATH = REPO_ROOT / "tests" / "hygiene_upstream_baseline.json"

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

# The account API's colon-less device id: a standalone 16- or 18-hex token.
COLONLESS_RE = re.compile(r"(?<![0-9A-Za-z_])(?:[0-9A-Fa-f]{18}|[0-9A-Fa-f]{16})(?![0-9A-Za-z_])")

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

# Synthetic colon-less tokens, upper-cased.
ALLOWED_COLONLESS_IDS = frozenset(
    {
        "0011223344556677",
        "0123456789ABCDEF",
        "1122334455667798E8",
        "8899AABBCCDDEEFF",
        "AABBCCDDEEFF0011",
        "AABBCCDDEEFF1122",
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

# --- upstream baseline ------------------------------------------------------

# Hex identifiers are hashed separator-free and upper-cased, so one value
# matches in every spelling.
HEX_CATEGORIES = frozenset({"device_ids", "macs", "colonless_ids"})


def fingerprint(category: str, value: str) -> str:
    """SHA-256 of ``value`` as the baseline stores it for ``category``."""
    if category in HEX_CATEGORIES:
        value = value.upper().replace(":", "").replace("-", "")
    return hashlib.sha256(value.encode()).hexdigest()


def _load_baseline() -> dict[str, frozenset[str]]:
    """Category -> upstream-owned hashes; empty when the file is absent."""
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {category: frozenset(hashes) for category, hashes in data["identifiers"].items()}


UPSTREAM_BASELINE = _load_baseline()


def _upstream_owned(category: str, value: str) -> bool:
    """Whether upstream's tree carries ``value`` in ``category``."""
    return fingerprint(category, value) in UPSTREAM_BASELINE.get(category, frozenset())


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


def _device_ids(line: str) -> list[str]:
    """Every device id in ``line``, colon or dashed spelling."""
    dashed = [m for m in DASHED_RE.findall(line) if m.count("-") != 5]
    return DEVICE_ID_RE.findall(line) + dashed


def _macs(line: str) -> list[str]:
    """Every six-octet MAC in ``line``, colon or dashed spelling."""
    dashed = [m for m in DASHED_RE.findall(line) if m.count("-") == 5]
    return MAC_RE.findall(line) + dashed


def _unlisted_hardware_ids(line: str) -> list[str]:
    """Device ids and MACs in ``line`` that are neither placeholders nor upstream's."""
    found = []
    for match in _device_ids(line):
        # An id is judged by its first eight octets; any extended tail is
        # address structure, not identity.
        octets = match.upper().replace("-", ":").split(":")
        if ":".join(octets[:8]) not in ALLOWED_DEVICE_IDS and not _upstream_owned("device_ids", match):
            found.append(match)
    for match in _macs(line):
        if match.upper().replace("-", ":") not in ALLOWED_MACS and not _upstream_owned("macs", match):
            found.append(match)
    return found


def _unlisted_colonless_ids(line: str) -> list[str]:
    """Colon-less device ids in ``line`` that are neither placeholders nor upstream's."""
    return [
        match
        for match in COLONLESS_RE.findall(line)
        if match.upper() not in ALLOWED_COLONLESS_IDS and not _upstream_owned("colonless_ids", match)
    ]


def _unlisted_iot_topics(line: str) -> list[str]:
    """AWS IoT topics in ``line`` that upstream does not ship."""
    return [match for match in IOT_TOPIC_RE.findall(line) if not _upstream_owned("iot_topics", match)]


def _global_ips(line: str) -> list[str]:
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


def _routable_ips(line: str) -> list[str]:
    """Globally-routable IPv4 addresses in ``line`` that upstream does not ship."""
    return [text for text in _global_ips(line) if not _upstream_owned("ips", text)]


def _is_placeholder(value: str) -> bool:
    """Whether a secret field's value is one of the listed stand-ins."""
    return value.strip().lower() in PLACEHOLDER_VALUES


def _all_secret_values(line: str) -> list[str]:
    """Account-secret fields in ``line`` carrying a non-placeholder value."""
    return [f"{field}={value}" for field, value in SECRET_FIELD_RE.findall(line) if not _is_placeholder(value)]


def _secret_values(line: str) -> list[str]:
    """Non-placeholder account-secret fields in ``line`` that upstream does not ship."""
    return [value for value in _all_secret_values(line) if not _upstream_owned("secrets", value)]


# Raw collectors per baseline category: every match, before any exemption.
BASELINE_COLLECTORS = {
    "device_ids": _device_ids,
    "macs": _macs,
    "colonless_ids": COLONLESS_RE.findall,
    "iot_topics": IOT_TOPIC_RE.findall,
    "ips": _global_ips,
    "secrets": _all_secret_values,
}


class TestNoRealIdentifiers:
    """Nothing tracked in this repo may identify a real account or device."""

    def test_no_unlisted_hardware_ids(self):
        hits = _scan(_unlisted_hardware_ids)

        assert hits == [], "Non-allowlisted device ids / MACs:\n" + "\n".join(hits)

    def test_no_unlisted_colonless_ids(self):
        hits = _scan(_unlisted_colonless_ids)

        assert hits == [], "Non-allowlisted colon-less device ids:\n" + "\n".join(hits)

    def test_no_aws_iot_topics(self):
        hits = _scan(_unlisted_iot_topics)

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
        dumps = [p for p in _tracked_files() if p.startswith(("docs/_research/", "docs/plans/", "docs/sweeps/"))]

        assert dumps == []


class TestUpstreamBaseline:
    """The baseline exempts upstream's identifiers and nothing else."""

    def test_the_baseline_is_committed(self):
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

        assert re.fullmatch(r"[0-9a-f]{40}", data["upstream_commit"])
        assert set(data["identifiers"]) == set(BASELINE_COLLECTORS)

    def test_the_baseline_holds_hashes_only(self):
        for hashes in _load_baseline().values():
            assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)

    # hygiene: exempt-begin
    def test_an_upstream_owned_value_passes(self, monkeypatch):
        sample = "device = 'A1:B2:C3:D4:E5:F6:07:08'"
        owned = {"device_ids": frozenset({fingerprint("device_ids", "a1-b2-c3-d4-e5-f6-07-08")})}
        monkeypatch.setattr(f"{__name__}.UPSTREAM_BASELINE", owned)

        assert _unlisted_hardware_ids(sample) == []

    def test_a_value_upstream_lacks_is_still_caught(self, monkeypatch):
        sample = "device = 'A1:B2:C3:D4:E5:F6:07:08'"
        owned = {"macs": frozenset({fingerprint("macs", "A1:B2:C3:D4:E5:F6")})}
        monkeypatch.setattr(f"{__name__}.UPSTREAM_BASELINE", owned)

        assert _unlisted_hardware_ids(sample) == ["A1:B2:C3:D4:E5:F6:07:08"]

    # hygiene: exempt-end


class TestTheGateScansItself:
    """The gate is a tracked file too, and used to be the one file exempt."""

    def test_this_file_is_in_the_scan_set(self):
        assert "tests/test_repo_hygiene.py" in _tracked_text_files()

    def test_the_exemption_is_three_bounded_blocks(self):
        """Only the allowlists and the deliberate samples are exempt."""
        source = _read("tests/test_repo_hygiene.py")
        lines = source.splitlines()

        opens = [n for n, line in enumerate(lines) if EXEMPT_BEGIN in line and "=" not in line]
        closes = [n for n, line in enumerate(lines) if EXEMPT_END in line and "=" not in line]
        assert len(opens) == len(closes) == 3
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
            (_unlisted_colonless_ids, "device_id = 'A1B2C3D4E5F60708'"),
            (_unlisted_colonless_ids, "device_id = 'A1B2C3D4E5F6070809'"),
            (_unlisted_iot_topics, "topic: GA/deadbeefdeadbeefdeadbeefdeadbeef"),
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
            (_unlisted_iot_topics, "topic: GA/<32-hex account topic>"),
            (_unlisted_colonless_ids, "device_id = 'AABBCCDDEEFF0011'"),
            (_unlisted_colonless_ids, "digest = '" + "ab" * 32 + "'"),
            (_routable_ips, "host = '10.20.0.51'"),
            (_routable_ips, "host = '192.0.2.205'"),
            (_secret_values, '"secretCode": "[REDACTED]"'),
        ],
    )
    def test_a_placeholder_is_not_caught(self, finder, sample: str):
        assert not finder(sample)

    # hygiene: exempt-end
