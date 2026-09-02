"""The CloudflareOrigin Caddyfile must trust Cloudflare's edge ranges.

Behind the orange cloud the peer Caddy sees is a Cloudflare edge; without
``trusted_proxies`` Caddy discards Cloudflare's ``X-Forwarded-For`` and the
relay attributes every apex request to Cloudflare — breaking per-IP rate
limits and the ad-consent network fingerprint, which has to match a sign-up
that reaches the gray-cloud ``app.mobaide.com`` directly. The LetsEncrypt
mode has no proxy in front and must trust nothing.
"""
from __future__ import annotations

import ipaddress
import re
from pathlib import Path

_PROD_DIR = Path(__file__).resolve().parents[2] / "deploy" / "prod"

# https://www.cloudflare.com/ips-v4 and /ips-v6, checked 2026-09-02.
CLOUDFLARE_RANGES = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)


def _trusted_proxy_ranges(filename: str) -> list[str]:
    source = (_PROD_DIR / filename).read_text(encoding="utf-8")
    match = re.search(r"^\s*trusted_proxies\s+static\s+(.+)$", source, re.M)
    return match.group(1).split() if match else []


def test_cloudflare_origin_trusts_exactly_the_published_edge_ranges() -> None:
    ranges = _trusted_proxy_ranges("Caddyfile.CloudflareOrigin")
    assert set(ranges) == set(CLOUDFLARE_RANGES)
    for cidr in ranges:
        ipaddress.ip_network(cidr)  # a typo here would fail `caddy validate` at deploy


def test_cloudflare_origin_global_options_come_first() -> None:
    """A global options block anywhere but first is a Caddyfile parse error."""
    source = (_PROD_DIR / "Caddyfile.CloudflareOrigin").read_text(encoding="utf-8")
    first_block = next(line for line in source.splitlines() if line and not line.startswith("#"))
    assert first_block.strip() == "{"


def test_lets_encrypt_mode_trusts_no_proxy() -> None:
    assert _trusted_proxy_ranges("Caddyfile.LetsEncrypt") == []
