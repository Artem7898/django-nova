#!/usr/bin/env python3
"""Updates coverage badge in README.md."""

import argparse
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"
COVERAGE_XML = ROOT / "coverage.xml"


def get_new_readme_content() -> str | None:
    if not COVERAGE_XML.exists():
        return None

    root = ET.parse(COVERAGE_XML).getroot()
    line_rate = float(root.get("line-rate", "0"))
    percent = int(line_rate * 100)

    if percent >= 90:
        color = "brightgreen"
    elif percent >= 75:
        color = "green"
    elif percent >= 50:
        color = "yellow"
    else:
        color = "red"

    # ИСПРАВЛЕНО: Добавлено .svg в конец URL
    badge_url = f"https://img.shields.io/badge/coverage-{percent}%-{color}.svg"

    content = README.read_text(encoding="utf-8")

    # Regex теперь корректно найдет старый URL (даже если там не было .svg)
    new_content = re.sub(
        r"!\[Coverage\]\(https://img\.shields\.io/badge/coverage-[^)]*\)",
        f"![Coverage]({badge_url})",
        content,
    )

    return new_content if new_content != content else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Update README coverage badge")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="Write changes to README.md")
    group.add_argument(
        "--check", action="store_true", help="Check if README.md badge is up to date"
    )
    args = parser.parse_args()

    new_content = get_new_readme_content()

    if args.check:
        if new_content is not None:
            print(
                "ERROR: README.md coverage badge is out of date. Run `python scripts/update_badge.py --write` to update.",
                file=sys.stderr,
            )
            return 1
        if not COVERAGE_XML.exists():
            print("WARNING: coverage.xml not found, skipping badge check.", file=sys.stderr)
        else:
            print("✅ README.md badge is up to date.")
        return 0

    if args.write:
        if new_content is not None:
            README.write_text(new_content, encoding="utf-8")
            print("✅  Updated coverage badge in README.md")
        else:
            if not COVERAGE_XML.exists():
                print("WARNING: coverage.xml not found, nothing to do.", file=sys.stderr)
            else:
                print("✅ README.md badge is already up to date.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
