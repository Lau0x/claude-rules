#!/usr/bin/env python3
"""Fetch https://ip.net.coffee/claude/site.html and regenerate Claude rule files.

Source of truth for generated files:
  1. The page's classical DOMAIN-SUFFIX / DOMAIN / ... <pre> block
  2. KYC domains from the page tables (not always in the copyable block)
  3. Optional keyword fallbacks from the geosite/keyword <pre> block,
     but only keywords not already covered by a DOMAIN-SUFFIX of the same word

This script never commits or pushes. It prints a unified diff when files change
and exits 2 if the page could not be parsed, 1 if files changed, 0 if unchanged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as htmlmod
import json
import re
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://ip.net.coffee/claude/site.html"
TZ = dt.timezone(dt.timedelta(hours=8), name="CST")

REPO_ROOT = Path(__file__).resolve().parent
RULES_DIR = REPO_ROOT / "rules"
SNAP_DIR = REPO_ROOT / "snapshots"

CLASSICAL_START = "DOMAIN-SUFFIX,anthropic.com"
USER_AGENT = "claude-rules-update/1.0 (+local rule sync; contact: repo owner)"


def now_cst() -> dt.datetime:
    return dt.datetime.now(TZ)


def fetch(url: str, timeout: int = 30) -> tuple[str, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        headers = {k.lower(): v for k, v in resp.headers.items()}
        charset = "utf-8"
        ctype = headers.get("content-type", "")
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        if m:
            charset = m.group(1)
        text = raw.decode(charset, errors="replace")
        meta = {
            "url": url,
            "final_url": resp.geturl(),
            "status": getattr(resp, "status", 200),
            "last_modified": headers.get("last-modified"),
            "date_header": headers.get("date"),
            "etag": headers.get("etag"),
            "cache_control": headers.get("cache-control"),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        return text, meta


def extract_pre_blocks(html: str) -> list[str]:
    blocks = []
    for raw in re.findall(r"<pre[^>]*>(.*?)</pre>", html, flags=re.S | re.I):
        text = re.sub(r"<[^>]+>", "", raw)
        text = htmlmod.unescape(text).strip()
        if text:
            blocks.append(text)
    return blocks


def parse_classical(blocks: list[str]) -> list[str]:
    for block in blocks:
        if CLASSICAL_START in block and "payload:" not in block[:80]:
            return normalize_rules(block)
    raise ValueError("could not find classical DOMAIN-SUFFIX rule block in page HTML")


def normalize_rules(block: str) -> list[str]:
    rules: list[str] = []
    seen = set()
    for line in block.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        # Keep only typed rules; drop policy if a copied config leaked one in
        parts = [p.strip() for p in raw.split(",")]
        if not parts:
            continue
        kind = parts[0].upper()
        if kind not in {
            "DOMAIN",
            "DOMAIN-SUFFIX",
            "DOMAIN-KEYWORD",
            "IP-CIDR",
            "IP-CIDR6",
            "IP-ASN",
        }:
            continue
        # Reconstruct without a trailing policy name. Keep no-resolve.
        value = parts[1] if len(parts) > 1 else ""
        flags = [p for p in parts[2:] if p.lower() in {"no-resolve"}]
        if kind == "IP-CIDR6" and value:
            # Normalize IPv6 hex to lowercase for stable diffs
            value = value.lower()
        rule = ",".join([kind, value] + flags)
        if rule not in seen:
            seen.add(rule)
            rules.append(rule)
    if not rules:
        raise ValueError("classical block parsed to zero rules")
    return rules


def parse_kyc(html: str) -> list[str]:
    """Pull KYC domains from tables (they are often omitted from the copyable block)."""
    wanted = []
    # Prefer explicit known names if present anywhere in a table row with KYC context.
    # Parse all tables: if the table heading/caption or nearby text is KYC, take domain cells.
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S | re.I)
    kyc_rules: list[str] = []
    seen = set()

    def add(kind: str, domain: str) -> None:
        rule = f"{kind},{domain}"
        if rule not in seen:
            seen.add(rule)
            kyc_rules.append(rule)

    # Reliable fallback list, but only include if the domain is actually mentioned on the page
    candidates = [
        ("DOMAIN", "maps.googleapis.com"),
        ("DOMAIN-SUFFIX", "withpersona.com"),
        ("DOMAIN-SUFFIX", "frompersona.com"),
    ]
    plain = htmlmod.unescape(re.sub(r"<[^>]+>", " ", html))
    for kind, domain in candidates:
        if domain in plain:
            add(kind, domain)

    # Also scrape code-like domains from a table that mentions KYC / Persona
    for table in tables:
        tplain = htmlmod.unescape(re.sub(r"<[^>]+>", " ", table))
        if not re.search(r"KYC|Persona|withpersona|frompersona", tplain, re.I):
            continue
        for domain in re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", tplain, re.I):
            d = domain.lower()
            if d in {"maps.googleapis.com"}:
                add("DOMAIN", d)
            elif d.endswith("persona.com") or d in {"withpersona.com", "frompersona.com"}:
                add("DOMAIN-SUFFIX", d)
    return kyc_rules


def parse_fallback_keywords(blocks: list[str], classical: list[str]) -> list[str]:
    """From the geosite/keyword block, keep keyword:X when not already a suffix X.io etc."""
    extra: list[str] = []
    covered_keywords = set()
    for rule in classical:
        parts = rule.split(",")
        if parts[0] == "DOMAIN-KEYWORD":
            covered_keywords.add(parts[1].lower())
        elif parts[0] in {"DOMAIN", "DOMAIN-SUFFIX"} and parts[1]:
            # sentry.io already covers official Sentry; still allow explicit keyword fallback
            pass
    for block in blocks:
        if "geosite:" in block or (block.lstrip().startswith("keyword:") or "\nkeyword:" in block):
            for line in block.splitlines():
                line = line.strip()
                m = re.match(r"keyword:([A-Za-z0-9_-]+)$", line, re.I)
                if not m:
                    continue
                kw = m.group(1).lower()
                if kw in covered_keywords:
                    continue
                rule = f"DOMAIN-KEYWORD,{kw}"
                if rule not in extra:
                    extra.append(rule)
    return extra


def render_list(title: str, source_meta: dict, rules: list[str], notes: list[str] | None = None) -> str:
    last_mod = source_meta.get("last_modified") or "unknown"
    lines = [
        f"# {title}",
        f"# Source: {SOURCE_URL}",
        f"# Page Last-Modified: {last_mod}",
        "# Format: Shadowrocket / Surge RULE-SET (no policy; assign policy in the RULE-SET line)",
        "# Do not put proxy credentials or subscription URLs in this repository.",
    ]
    if notes:
        for n in notes:
            lines.append(f"# {n}")
    lines.append("")
    lines.extend(rules)
    lines.append("")
    return "\n".join(lines)


def render_clash_yaml(source_meta: dict, rules: list[str]) -> str:
    last_mod = source_meta.get("last_modified") or "unknown"
    lines = [
        "# Clash / Mihomo classical payload",
        f"# Source: {SOURCE_URL}",
        f"# Page Last-Modified: {last_mod}",
        "payload:",
    ]
    for rule in rules:
        lines.append(f"  - {rule}")
    lines.append("")
    return "\n".join(lines)


def unified_diff(path: Path, new_text: str) -> str:
    import difflib

    old = path.read_text() if path.exists() else ""
    old_lines = old.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    if old_lines == new_lines:
        return ""
    from_name = str(path) if path.exists() else "/dev/null"
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_name,
            tofile=str(path),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate Claude rule files from the Net.Coffee page")
    parser.add_argument("--html", type=Path, help="Use a local HTML snapshot instead of fetching")
    parser.add_argument("--dry-run", action="store_true", help="Print diff only, do not write files")
    args = parser.parse_args()

    fetched_at = now_cst().strftime("%Y-%m-%d %H:%M:%S CST")
    if args.html:
        raw = args.html.read_bytes()
        html = raw.decode("utf-8", errors="replace")
        meta = {
            "url": SOURCE_URL,
            "final_url": f"file://{args.html}",
            "status": 200,
            "last_modified": None,
            "date_header": None,
            "etag": None,
            "cache_control": None,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "from_local_file": str(args.html),
        }
    else:
        html, meta = fetch(SOURCE_URL)
    meta["fetched_at_cst"] = fetched_at

    blocks = extract_pre_blocks(html)
    classical = parse_classical(blocks)
    extras = parse_fallback_keywords(blocks, classical)
    # keyword:sentry is in the fallback block and is useful; include it in the main list
    main_rules = classical[:]
    for rule in extras:
        if rule not in main_rules:
            main_rules.append(rule)
    kyc_rules = parse_kyc(html)
    # Hash the parsed rules so HTML-only noise (CDN, analytics) is not treated as a rule change.
    parsed_blob = "\n".join(main_rules + ["---kyc---"] + kyc_rules).encode()
    meta["parsed_rules_sha256"] = hashlib.sha256(parsed_blob).hexdigest()
    meta["classical_count"] = len(classical)
    meta["main_count"] = len(main_rules)
    meta["kyc_count"] = len(kyc_rules)

    ntp_rules = [
        "DOMAIN-SUFFIX,pool.ntp.org",
        "DOMAIN-SUFFIX,ntp.org",
        "DOMAIN,time.apple.com",
        "DOMAIN,time.asia.apple.com",
        "DOMAIN,time.euro.apple.com",
        "DOMAIN,time.windows.com",
        "DOMAIN,time.cloudflare.com",
        "DOMAIN,time.google.com",
        "DOMAIN,time.nist.gov",
        "DOMAIN,ntp.ubuntu.com",
    ]

    outputs = {
        RULES_DIR / "claude.list": render_list(
            "Claude / Anthropic rules",
            meta,
            main_rules,
            notes=[
                "DOMAIN-KEYWORD,sentry comes from the page fallback section (not the classical copy block).",
                "cdn/mcp/console/workbench.anthropic.com are covered by DOMAIN-SUFFIX,anthropic.com.",
            ],
        ),
        RULES_DIR / "claude-kyc.list": render_list(
            "Claude KYC domains (optional, recommended if you use Anthropic identity checks)",
            meta,
            kyc_rules,
            notes=["These appear in the page tables but are often omitted from the copyable rule block."],
        ),
        RULES_DIR / "claude-ntp.list": render_list(
            "Optional NTP domains (Shadowrocket has no geosite:category-ntp)",
            meta,
            ntp_rules,
            notes=[
                "The page recommends geosite:category-ntp; this is a conservative stand-in, not a full geosite dump.",
                "Place this RULE-SET BEFORE any DOMAIN-SUFFIX,apple.com,DIRECT rule.",
                "NTP itself returns UTC, not a timezone. Also set the device timezone to match the proxy exit.",
                "This list is maintained in the script, not parsed from the page (the page only names geosite:category-ntp).",
            ],
        ),
        RULES_DIR / "claude.yaml": render_clash_yaml(meta, main_rules + kyc_rules),
        SNAP_DIR / "source-meta.json": json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
    }

    rule_paths = [path for path in outputs if path.parent == RULES_DIR]
    rule_diffs = []
    for path in rule_paths:
        d = unified_diff(path, outputs[path])
        if d:
            rule_diffs.append(d)

    print(f"Fetched: {meta.get('final_url')} at {fetched_at}")
    print(f"Last-Modified: {meta.get('last_modified') or 'n/a'}")
    print(f"HTML SHA256: {meta['sha256']}")
    print(f"Parsed rules SHA256: {meta.get('parsed_rules_sha256')}")
    print(f"Classical rules: {len(classical)}; main list: {len(main_rules)}; KYC: {len(kyc_rules)}")

    if args.dry_run:
        if rule_diffs:
            print("\nRule files would change:\n")
            sys.stdout.write("".join(rule_diffs))
            return 1
        print("No rule file changes.")
        return 0

    RULES_DIR.mkdir(parents=True, exist_ok=True)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content)

    if rule_diffs:
        print("\nRule files changed:\n")
        sys.stdout.write("".join(rule_diffs))
        return 1
    print("No rule file changes.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — CLI
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
