"""Tests for translation file consistency."""

import json
from pathlib import Path


def _get_keys(obj: dict | list | str, prefix: str = "") -> set[str]:
    """Recursively extract all key paths from a nested dict."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            keys.add(full_key)
            keys.update(_get_keys(v, full_key))
    return keys


def test_strings_and_en_json_have_identical_keys():
    """Verify strings.json and translations/en.json have matching key structures."""
    base = Path(__file__).resolve().parent.parent / "custom_components" / "govee"

    strings_path = base / "strings.json"
    en_path = base / "translations" / "en.json"

    assert strings_path.exists(), f"Missing {strings_path}"
    assert en_path.exists(), f"Missing {en_path}"

    with open(strings_path) as f:
        strings_data = json.load(f)
    with open(en_path) as f:
        en_data = json.load(f)

    strings_keys = _get_keys(strings_data)
    en_keys = _get_keys(en_data)

    missing_in_en = strings_keys - en_keys
    extra_in_en = en_keys - strings_keys

    errors = []
    if missing_in_en:
        errors.append(f"Keys in strings.json missing from en.json:\n  {missing_in_en}")
    if extra_in_en:
        errors.append(f"Keys in en.json not in strings.json:\n  {extra_in_en}")

    assert not errors, "\n".join(errors)


def test_strings_json_is_valid():
    """Verify strings.json is valid JSON."""
    path = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "govee"
        / "strings.json"
    )
    with open(path) as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert "config" in data
    assert "entity" in data


def test_en_json_is_valid():
    """Verify translations/en.json is valid JSON."""
    path = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "govee"
        / "translations"
        / "en.json"
    )
    with open(path) as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert "config" in data
    assert "entity" in data
