"""Check the repository against the HACS and hassfest requirements.

``hassfest`` (run in CI) validates the Home Assistant side of a custom
integration. This script covers the checks HACS itself performs, plus a few
consistency checks that are easy to break by hand, so contributors can verify
everything locally in one second:

    python scripts/check_repo.py

It intentionally implements only cheap structural checks; hassfest remains the
authority for manifest, config flow and translation semantics.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ROOT / "custom_components"

#: Keys accepted by the HACS manifest schema (extra keys fail validation).
HACS_JSON_KEYS = {
    "name",
    "content_in_root",
    "country",
    "filename",
    "hacs",
    "hide_default_branch",
    "homeassistant",
    "persistent_directory",
    "render_readme",
    "zip_release",
}

HASSFEST_MANIFEST_KEYS = {
    "domain",
    "name",
    "after_dependencies",
    "codeowners",
    "config_flow",
    "dependencies",
    "documentation",
    "integration_type",
    "iot_class",
    "issue_tracker",
    "loggers",
    "quality_scale",
    "requirements",
    "version",
}

IOT_CLASSES = {
    "assumed_state",
    "calculated",
    "cloud_polling",
    "cloud_push",
    "local_polling",
    "local_push",
}

INTEGRATION_TYPES = {"device", "entity", "hardware", "helper", "hub", "service", "system"}

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([a-z]+\d+)?$")

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    """Record a failure when ``condition`` is false."""
    if not condition:
        failures.append(message)


def load_json(path: Path) -> dict:
    """Load a JSON file, recording a failure when it is malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        failures.append(f"{path.relative_to(ROOT)}: cannot be parsed ({err})")
        return {}


def png_size(path: Path) -> tuple[int, int] | None:
    """Return the dimensions of a PNG file."""
    try:
        header = path.read_bytes()[:24]
    except OSError:
        return None
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def check_layout() -> Path | None:
    """Check that the repository holds exactly one integration."""
    check(COMPONENTS.is_dir(), "custom_components/ is missing")
    if not COMPONENTS.is_dir():
        return None
    integrations = [item for item in COMPONENTS.iterdir() if item.is_dir()]
    check(
        len(integrations) == 1,
        f"custom_components/ must contain exactly one integration, found {len(integrations)}",
    )
    check((ROOT / "README.md").is_file(), "README.md is missing")
    check(not (ROOT / "manifest.json").exists(), "a manifest.json must not live at the repo root")
    return integrations[0] if integrations else None


def check_manifest(integration: Path) -> dict:
    """Check manifest.json against the hassfest and HACS rules."""
    manifest_path = integration / "manifest.json"
    check(manifest_path.is_file(), "manifest.json is missing")
    manifest = load_json(manifest_path) if manifest_path.is_file() else {}

    for key in ("domain", "name", "codeowners", "documentation", "issue_tracker", "version"):
        check(key in manifest, f"manifest.json is missing '{key}'")
    check(
        manifest.get("domain") == integration.name,
        f"manifest domain '{manifest.get('domain')}' does not match the directory name",
    )
    check(
        bool(VERSION_RE.match(str(manifest.get("version", "")))),
        f"manifest version '{manifest.get('version')}' is not a simple version like 1.2.3",
    )
    check(
        str(manifest.get("documentation", "")).startswith("https://"),
        "manifest documentation must be an https URL",
    )
    check(
        str(manifest.get("issue_tracker", "")).startswith("https://"),
        "manifest issue_tracker must be an https URL",
    )
    check(
        manifest.get("iot_class") in IOT_CLASSES,
        f"manifest iot_class must be one of {sorted(IOT_CLASSES)}",
    )
    check(
        manifest.get("integration_type", "hub") in INTEGRATION_TYPES,
        f"manifest integration_type must be one of {sorted(INTEGRATION_TYPES)}",
    )
    unknown = set(manifest) - HASSFEST_MANIFEST_KEYS
    check(not unknown, f"manifest.json has unknown keys: {sorted(unknown)}")

    keys = list(manifest)
    expected = ["domain", "name", *sorted(key for key in keys if key not in ("domain", "name"))]
    check(keys == expected, "manifest.json keys must be sorted (domain, name, then alphabetical)")

    if manifest.get("config_flow"):
        check(
            (integration / "config_flow.py").is_file(),
            "config_flow is true but config_flow.py is missing",
        )
    return manifest


def check_hacs_json() -> None:
    """Check hacs.json against the HACS manifest schema."""
    path = ROOT / "hacs.json"
    check(path.is_file(), "hacs.json is missing")
    if not path.is_file():
        return
    data = load_json(path)
    check("name" in data, "hacs.json must define 'name'")
    unknown = set(data) - HACS_JSON_KEYS
    check(not unknown, f"hacs.json has keys HACS does not accept: {sorted(unknown)}")
    check(
        not data.get("zip_release") or "filename" in data,
        "hacs.json sets zip_release without filename",
    )
    if "homeassistant" in data:
        check(
            re.match(r"^\d{4}\.\d+(\.\d+)?(b\d+)?$", str(data["homeassistant"])) is not None,
            "hacs.json 'homeassistant' must look like 2025.1.0",
        )


def check_brand(integration: Path) -> None:
    """Check the local brand images HACS requires."""
    icon = integration / "brand" / "icon.png"
    check(icon.is_file(), f"{icon.relative_to(ROOT)} is missing (HACS requires it)")
    if icon.is_file():
        size = png_size(icon)
        check(size is not None, "brand/icon.png is not a valid PNG")
        check(size == (256, 256), f"brand/icon.png should be 256x256, found {size}")
    logo = integration / "brand" / "logo.png"
    if logo.is_file():
        size = png_size(logo)
        check(size is not None, "brand/logo.png is not a valid PNG")
        if size:
            check(
                128 <= min(size) <= 256,
                f"brand/logo.png shortest side should be 128-256, found {min(size)}",
            )


def check_translations(integration: Path) -> None:
    """Check that strings.json and translations/en.json stay in sync."""
    strings = integration / "strings.json"
    english = integration / "translations" / "en.json"
    check(strings.is_file(), "strings.json is missing")
    check(english.is_file(), "translations/en.json is missing")
    if strings.is_file() and english.is_file():
        check(
            load_json(strings) == load_json(english),
            "translations/en.json must match strings.json",
        )


def check_frontend_assets(integration: Path, manifest: dict) -> None:
    """Check that the files the integration serves over HTTP exist."""
    www = integration / "www"
    check(www.is_dir(), "the www directory with the Lovelace card is missing")
    for name in ("generic-video-proxy-card.js", "hls.light.min.js"):
        check((www / name).is_file(), f"www/{name} is missing")
    card = www / "generic-video-proxy-card.js"
    if card.is_file():
        source = card.read_text(encoding="utf-8")
        check("customElements.define" in source, "the card does not define a custom element")
        check(
            "domain" in manifest and manifest["domain"] in Path(integration).name,
            "the card and the integration domain disagree",
        )


def main() -> int:
    """Run every check and report the result."""
    integration = check_layout()
    if integration is not None:
        manifest = check_manifest(integration)
        check_brand(integration)
        check_translations(integration)
        check_frontend_assets(integration, manifest)
    check_hacs_json()

    if failures:
        print("Repository check failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Repository check passed: layout, manifest, hacs.json, brand, translations, assets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
