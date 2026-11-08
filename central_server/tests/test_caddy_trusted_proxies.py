"""The prod Caddyfiles must hand the relay one unspoofable client address.

The address must come from ``Cf-Connecting-Ip`` only: Cloudflare appends to a
client-written ``X-Forwarded-For``, so its leftmost entry — the one uvicorn
reads — is attacker-controlled. Every ``reverse_proxy`` in both modes replaces
``X-Forwarded-For`` with Caddy's resolved ``{client_ip}``.
"""
from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest

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


_CADDYFILES = ("Caddyfile.CloudflareOrigin", "Caddyfile.LetsEncrypt")


def _directive_lines(filename: str) -> list[str]:
    """Stripped non-blank lines with ``#`` comments removed."""
    source = (_PROD_DIR / filename).read_text(encoding="utf-8")
    lines = (re.sub(r"(^|\s)#.*$", "", line).strip() for line in source.splitlines())
    return [line for line in lines if line]


def _block_body(lines: list[str], opener: int) -> list[str]:
    """Directive lines inside the block opened on ``lines[opener]``."""
    depth = 0
    for index in range(opener, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if depth == 0:
            return lines[opener + 1:index]
    raise AssertionError(f"unclosed block at {lines[opener]!r}")


def _reverse_proxy_blocks(filename: str) -> list[list[str]]:
    lines = _directive_lines(filename)
    openers = [i for i, line in enumerate(lines) if line.split()[0] == "reverse_proxy"]
    for i in openers:
        assert lines[i].endswith("{"), f"{filename}: {lines[i]!r} has no header_up block"
    return [_block_body(lines, i) for i in openers]


def _client_ip_header_lines(filename: str) -> list[list[str]]:
    return [
        line.split()[1:]
        for line in _directive_lines(filename)
        if line.split()[0] == "client_ip_headers"
    ]


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


def test_cloudflare_origin_reads_the_client_ip_only_from_cf_connecting_ip() -> None:
    """An ``X-Forwarded-For`` fallback here would reopen the spoof."""
    lines = _directive_lines("Caddyfile.CloudflareOrigin")
    global_options = _block_body(lines, lines.index("{"))
    assert "client_ip_headers Cf-Connecting-Ip" in global_options
    assert _client_ip_header_lines("Caddyfile.CloudflareOrigin") == [["Cf-Connecting-Ip"]]


def test_lets_encrypt_mode_reads_no_client_ip_header() -> None:
    """With no trusted proxy in front, any client IP header is client-written."""
    assert _client_ip_header_lines("Caddyfile.LetsEncrypt") == []


@pytest.mark.parametrize("filename", _CADDYFILES)
def test_every_reverse_proxy_replaces_x_forwarded_for_with_the_resolved_client_ip(
    filename: str,
) -> None:
    blocks = _reverse_proxy_blocks(filename)
    assert len(blocks) >= 2  # the (app_mobaide) @backend handler and the apex block
    for body in blocks:
        xff = [line for line in body if re.search(r"(?i)\bX-Forwarded-For\b", line)]
        assert xff == ["header_up X-Forwarded-For {client_ip}"]
