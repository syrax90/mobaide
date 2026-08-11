#!/usr/bin/env python3
"""tls_preflight.py — classify TLS failures instead of guessing at them.

Every helper here returns data and prints nothing: deploy_central owns the
`step/ok/warn/fail/dim` emitters and renders the results. That also keeps the
module importable from both scripts without a cycle (deploy_central imports it
for `wait_http`; deploy_web imports both).

The classification exists because four unrelated faults all surface as one
opaque "could not reach the host":
  - the DNS record does not exist        -> kind "dns"
  - the port is closed / host is down    -> kind "connect" / "timeout"
  - THIS interpreter trusts nothing      -> kind "tls_local"
  - the host serves a bad certificate    -> kind "tls_server"
The last two raise an identical CERTIFICATE_VERIFY_FAILED, so they are told
apart by a control probe against a known-good public host. When that probe
cannot run either, the verdict is kind "inconclusive" — a fifth answer rather
than a guess at one of the other four.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterable, NamedTuple, Sequence

# Two hosts, pass on the first that verifies. A single blocked host must not
# produce a false "your CA store is broken" verdict — that is the one wrong
# answer that sends an operator to fix a workstation while the server burns.
# Ordered fewest-DNS-records-first: CONTROL_TIMEOUT is applied by
# create_connection to EVERY address a host resolves to, so a filtered host
# costs timeout x record count. www.google.com resolves to 8 addresses here
# (letsencrypt.org to 2), so putting it first made the common
# "Google is DROP-filtered on this network" case stall 64s before the second
# host was even tried.
CONTROL_URLS = ("https://letsencrypt.org/", "https://www.google.com/")

CONTROL_TIMEOUT = 8

_RDN_SHORT = {
    "countryName": "C",
    "stateOrProvinceName": "ST",
    "localityName": "L",
    "organizationName": "O",
    "organizationalUnitName": "OU",
    "commonName": "CN",
}

_DNS_SAN_RE = re.compile(r"DNS:([^,\s]+)")


class ProbeFailure(NamedTuple):
    kind: str
    detail: str
    remedy: str


class TlsWarning(NamedTuple):
    """A finding about the server's TLS material: headline + remedy lines.

    `kind` (`shadow`, `pair`, `missing`, `absent`, `unchecked`, `expiry`) lets
    callers decide which reassurance they are still entitled to print.

    `severity` decides how it is painted. A confirmed defect and "I could not
    determine this" must not look alike, and neither should a cert that expires
    next month — a red banner on every deploy for thirty days is the alarm
    fatigue that let the original incident's log line go unread.
    """

    kind: str
    headline: str
    lines: tuple[str, ...]
    severity: str = "fault"  # fault | advisory


class OriginProbe(NamedTuple):
    """What the remote preflight round-trip reported about the origin cert.

    `sans_read` distinguishes "openssl printed no SAN extension" from "openssl
    could not read this file at all". Both arrive as an empty `sans`, and
    conflating them makes the guard assert a clean bill of health for a cert it
    never parsed — the one failure mode worse than having no guard.
    """

    present: bool
    sans: tuple[str, ...]
    cert_pubkey: str
    key_pubkey: str
    sans_read: bool = False
    not_before: datetime | None = None
    not_after: datetime | None = None


def _default_context_trusts_anything() -> bool:
    """Can a stock SSLContext verify at all?

    Probing beats inferring from `get_default_verify_paths()`: on Windows that
    reports no cafile/capath at all, yet verification works because
    `load_default_certs()` pulls the ROOT/CA stores. Inferring there would swap
    the OS trust store for certifi on every Windows deploy, which silently
    breaks anyone behind a corporate TLS-inspecting proxy whose root lives only
    in that store.

    `get_ca_certs()` alone is not a complete oracle either: CPython loads a
    capath directory lazily, so a populated hashed capath reports zero certs
    until one is used. Hence the directory fallback — it only ever widens
    "trusts something", and it cannot mask the case this exists for, because
    an interpreter with no CA bundle reports capath=None rather than an empty
    directory. Every uncertain answer resolves to True, i.e. leave the
    operator's own trust store alone: substituting certifi is the action that
    changes which roots verify production, so it needs positive evidence.
    """
    try:
        if ssl.create_default_context().get_ca_certs():
            return True
        capath = ssl.get_default_verify_paths().capath
        return bool(capath and os.listdir(capath))
    except Exception:
        return True


def build_ssl_context() -> tuple[ssl.SSLContext | None, str | None]:
    """(context, warning). A None context means urllib's default is fine.

    python.org framework builds ship with no CA bundle until
    Install Certificates.command runs, leaving the interpreter unable to
    verify ANY https:// URL. certifi carries the same Mozilla roots, so fall
    back to it rather than making the operator export SSL_CERT_FILE by hand.
    An explicitly set SSL_CERT_FILE/SSL_CERT_DIR is left alone even when it
    points at nothing — overriding a deliberate choice would hide the mistake.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return None, None
    if _default_context_trusts_anything():
        return None, None
    try:
        import certifi
    except ImportError:
        return None, None
    return (
        ssl.create_default_context(cafile=certifi.where()),
        f"This Python trusts no certificate authorities "
        f"({ssl.get_default_verify_paths().openssl_cafile} is missing) — "
        f"verifying against certifi instead. {_permanent_ca_fix()}",
    )


def _permanent_ca_fix() -> str:
    if sys.platform == "darwin":
        return (
            "Permanent fix: run 'Install Certificates.command' from your "
            "Python install directory."
        )
    return "Permanent fix: install your platform's CA bundle for this interpreter."


def certifi_remedy() -> str:
    paths = ssl.get_default_verify_paths()
    lines = [
        "This is a workstation problem, not a server problem — the server may",
        "be perfectly healthy. Nothing this Python fetches over HTTPS can be",
        f"verified. CA bundle: {paths.cafile!r} (expected {paths.openssl_cafile}).",
        "Fix, in order of preference:",
    ]
    if sys.platform == "darwin":
        lines += [
            "  1. Run 'Install Certificates.command' from your Python install dir",
            "     (macOS python.org builds ship without a CA bundle).",
        ]
    else:
        lines.append("  1. Install your platform's CA bundle for this interpreter.")
    lines += [
        "  2. pip install certifi   (this script then picks it up automatically)",
        '  3. export SSL_CERT_FILE="$(python -c \'import certifi;print(certifi.where())\')"',
        "Note certifi does NOT contain the Cloudflare Origin CA, so this will",
        "not make an origin-cert host verify — see docs/deployment.md.",
    ]
    return "\n".join(lines)


def cloudflare_origin_remedy(code: int, host: str) -> str:
    lines = [
        "This is a CloudflareOrigin-mode fault on the edge->origin leg, so it",
        f"is invisible to a browser-style check of {host}.",
    ]
    if code == 525:
        lines += [
            "525 is an SSL handshake failure between Cloudflare and the origin —",
            "most often a cert/key pair that do not match, or Caddy not serving",
            "the origin cert at all (the container failed to start after a cert",
            "swap). Note a mismatched pair survives `caddy reload` and only",
            "bricks the NEXT container restart.",
        ]
    else:
        lines += [
            f"{code} means Cloudflare rejected the origin certificate under",
            "Full (strict) — typically expired, or missing this hostname from",
            "its SANs.",
        ]
    lines += [
        "Check the origin directly, bypassing the edge:",
        f"  ssh mobaide 'openssl s_client -connect localhost:443 -servername {host} "
        "</dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName'",
        "  ssh mobaide 'docker compose -f /opt/mobaide/docker-compose.prod.yml logs caddy | tail -50'",
        "Then: python deploy/prod/deploy_central.py --diagnose-tls --tls-mode CloudflareOrigin",
    ]
    return "\n".join(lines)


def inconclusive_remedy(host: str, port: int, timeout: int) -> str:
    """Say the attribution is unresolved, and show the served cert regardless.

    Deliberately does NOT claim "this is a workstation problem": no control
    host answered, so the evidence for that claim was never gathered.
    """
    lines = [
        "The attribution is INCONCLUSIVE. Either this machine trusts nothing,",
        "or the server is serving a certificate it should not be — the control",
        "hosts that would settle it were unreachable (egress filtering, an",
        "offline workstation, or an on-path attacker suppressing them).",
    ]
    try:
        issuer, sans = peer_cert_facts(host, port, timeout)
    except Exception:
        lines += [
            f"Inspect what {host} serves — this needs no trust store:",
            f"  openssl s_client -connect {host}:{port} -servername {host} "
            "</dev/null 2>/dev/null | openssl x509 -noout -issuer -ext subjectAltName",
        ]
        return "\n".join(lines)
    lines.append(f"{host} currently serves a cert issued by {issuer!r}.")
    lines.append(explain_server_cert(issuer, sans, host))
    lines.append(
        "If that issuer looks correct and public, suspect the local store: "
        f"{ssl.get_default_verify_paths().cafile!r}."
    )
    return "\n".join(lines)


def create_dns_remedy(host: str) -> str:
    lines = [f"Create the DNS record for {host}."]
    # Only the SPA host is gray-cloud in both modes; saying so for the apex or
    # api host would be the exact opposite of the right advice under
    # CloudflareOrigin, where those records are proxied.
    if host.startswith("app."):
        lines += [
            "That record is DNS only (gray cloud) in BOTH TLS modes; then run",
            "deploy_central.py so Caddy picks up the site block.",
        ]
    else:
        lines.append(
            "Under CloudflareOrigin this record is Proxied (orange cloud); "
            "under LetsEncrypt it is DNS only (gray cloud)."
        )
    return "\n".join(lines)


def san_covers_host(san: str, host: str) -> bool:
    """RFC 6125 wildcard semantics, which is also what Caddy applies when it
    decides a loaded certificate already covers a host and skips ACME:
    `*.example.com` covers `app.example.com`, but not `example.com` (no label
    to match) and not `a.b.example.com` (a wildcard spans exactly one label).
    """
    san = san.strip().rstrip(".").lower()
    host = host.strip().rstrip(".").lower()
    if not san or not host:
        return False
    if not san.startswith("*."):
        return san == host
    suffix = san[1:]
    if not host.endswith(suffix):
        return False
    label = host[: -len(suffix)]
    return bool(label) and "." not in label


def shadowing_sans(sans: Sequence[str], host: str) -> list[str]:
    return [san for san in sans if san_covers_host(san, host)]


def parse_openssl_san_output(text: str) -> list[str]:
    """DNS names out of `openssl x509 -noout -ext subjectAltName` output.

    Tolerates the newline-squashed form the remote preflight sends back, and
    returns [] when the certificate carries no SAN extension at all (openssl
    prints nothing, and an empty result must not read as "no match").
    """
    return _DNS_SAN_RE.findall(text or "")


def parse_openssl_dates(text: str) -> tuple[datetime | None, datetime | None]:
    """`notBefore=... notAfter=...` from `openssl x509 -startdate -enddate`.

    openssl pads single-digit days to two columns (`Aug  6`), so whitespace is
    normalised before parsing. Either field is None when absent or unparseable
    — never a guessed date, since the caller treats a missing value as "not
    checked" rather than "valid".
    """
    found: dict[str, datetime | None] = {"notBefore": None, "notAfter": None}
    for key in found:
        match = re.search(rf"{key}=([^=]*?)(?:\s+not(?:Before|After)=|$)", text or "")
        if not match:
            continue
        raw = " ".join(match.group(1).split())
        for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
            try:
                parsed = datetime.strptime(raw, fmt)
            except ValueError:
                continue
            found[key] = parsed.replace(tzinfo=timezone.utc)
            break
    return found["notBefore"], found["notAfter"]


def parse_origin_probe(lines: Iterable[str]) -> OriginProbe:
    """Read the ORIGIN_* facts out of the remote preflight's stdout.

    `ORIGIN_SANS=` is emitted only when openssl actually produced output, so
    its presence — not the PEM file's — is what sets `sans_read`. An unreadable
    cert and one with no SAN extension both leave it False, which is the safe
    direction: neither can be certified as "does not shadow app.<domain>".
    """
    sans: tuple[str, ...] = ()
    cert_pub = ""
    key_pub = ""
    present = False
    sans_read = False
    not_before: datetime | None = None
    not_after: datetime | None = None
    for line in lines:
        if line.startswith("ORIGIN_PRESENT="):
            present = True
        elif line.startswith("ORIGIN_SANS="):
            present = True
            sans_read = True
            sans = tuple(parse_openssl_san_output(line[len("ORIGIN_SANS="):]))
        elif line.startswith("ORIGIN_CERT_PUB="):
            cert_pub = line[len("ORIGIN_CERT_PUB="):].strip()
        elif line.startswith("ORIGIN_KEY_PUB="):
            key_pub = line[len("ORIGIN_KEY_PUB="):].strip()
        elif line.startswith("ORIGIN_DATES="):
            not_before, not_after = parse_openssl_dates(line[len("ORIGIN_DATES="):])
    return OriginProbe(
        present, sans, cert_pub, key_pub, sans_read, not_before, not_after
    )


def proxied_hosts(domain: str) -> tuple[str, ...]:
    """The hosts whose Caddy site blocks load the origin cert by path."""
    return (domain, f"www.{domain}", f"api.{domain}")


EXPIRY_WARN_DAYS = 30


def _validity_warning(probe: OriginProbe, now: datetime) -> TlsWarning | None:
    """Expired, not-yet-valid, or expiring soon.

    Worth checking here because the deploy's forced reload is what makes staged
    on-disk certificate content take effect, so an expired PEM sitting in
    tls/ goes live at deploy time rather than waiting for a container restart.
    A public probe cannot catch it in CloudflareOrigin mode — that verifies the
    edge certificate, not the origin's.
    """
    if probe.not_after is None:
        return None
    window = (
        f"valid {probe.not_before:%Y-%m-%d} to {probe.not_after:%Y-%m-%d}"
        if probe.not_before
        else f"expires {probe.not_after:%Y-%m-%d}"
    )
    if probe.not_after <= now:
        return TlsWarning(
            "expiry",
            f"Origin cert EXPIRED on {probe.not_after:%Y-%m-%d} ({window})",
            (
                "Cloudflare rejects an expired origin certificate with a 526 on",
                "every proxied host — the apex, www and api, i.e. every agent",
                "tunnel and mobile client, not just the SPA.",
                "Re-issue it before deploying: this deploy's forced reload is what",
                "loads whatever is on disk.",
            ),
        )
    if probe.not_before is not None and probe.not_before > now:
        return TlsWarning(
            "expiry",
            f"Origin cert is not valid until {probe.not_before:%Y-%m-%d} ({window})",
            (
                "Cloudflare rejects a not-yet-valid certificate the same way it",
                "rejects an expired one. Check the server clock if this is",
                "unexpected.",
            ),
        )
    if probe.not_after - now <= timedelta(days=EXPIRY_WARN_DAYS):
        days = (probe.not_after - now).days
        return TlsWarning(
            "expiry",
            f"Origin cert expires in {days} day{'s' if days != 1 else ''} "
            f"({probe.not_after:%Y-%m-%d})",
            (
                "Nothing renews this certificate automatically — Cloudflare Origin",
                "certs are operator-managed, unlike the ACME-issued app host.",
                "Re-issue and rotate per docs/deployment.md before it lapses.",
            ),
            "advisory",
        )
    return None


def origin_cert_warnings(
    probe: OriginProbe, domain: str, now: datetime | None = None
) -> list[TlsWarning]:
    """The ways the server's origin cert can be wrong before a deploy.

    Both directions of the SAN list matter. Covering `app.<domain>` breaks the
    SPA; failing to cover a proxied host breaks that host outright under
    Cloudflare's Full (strict), and `api.<domain>` carries every agent and
    mobile client. The runbook now asks the operator to hand-edit that list in
    the dashboard, so checking only one direction would bless half the edit.
    """
    if not probe.present:
        return []
    warnings: list[TlsWarning] = []
    app_host = f"app.{domain}"
    covering = shadowing_sans(probe.sans, app_host)
    if covering:
        warnings.append(
            TlsWarning(
                "shadow",
                f"Origin cert SAN {covering[0]!r} covers {app_host} — the browser "
                f"SPA will serve a certificate no browser trusts",
                (
                    "Caddy's certificate cache is global, so a loaded cert matching",
                    f"{app_host} disables automatic HTTPS for it. Caddy logs",
                    '"skipping automatic certificate management because one or more',
                    'matching certificates are already loaded" and never asks',
                    "Let's Encrypt, so the host answers with the Cloudflare Origin",
                    "CA cert, which browsers reject.",
                    "Fix: re-issue the Cloudflare Origin certificate for exactly",
                    f"  {domain}, www.{domain}, api.{domain}",
                    "(the dashboard pre-fills a wildcard — delete it), replace",
                    "/opt/mobaide/tls/origin-cert.pem + origin-key.pem, and reload",
                    "with --force. See docs/deployment.md.",
                ),
            )
        )
    # Gated on sans_read for the same reason the caller gates its reassurances:
    # an unreadable SAN list is not evidence that a host is uncovered.
    if probe.sans_read:
        uncovered = [
            host
            for host in proxied_hosts(domain)
            if not any(san_covers_host(san, host) for san in probe.sans)
        ]
        if uncovered:
            warnings.append(
                TlsWarning(
                    "missing",
                    f"Origin cert does not cover {', '.join(uncovered)} — "
                    f"{'those hosts' if len(uncovered) > 1 else 'that host'} "
                    f"will fail TLS at the Cloudflare edge",
                    (
                        "Caddy serves these hosts from this certificate, so a name",
                        "missing from its SANs is a 526 under Cloudflare's",
                        "Full (strict) mode. api.<domain> carries every agent tunnel",
                        "and mobile client, not just the SPA.",
                        "Re-issue the origin certificate for exactly:",
                        f"  {', '.join(proxied_hosts(domain))}",
                        f"Currently present: {', '.join(probe.sans) or '(none)'}",
                    ),
                )
            )
    validity = _validity_warning(probe, now or datetime.now(timezone.utc))
    if validity is not None:
        warnings.append(validity)
    if probe.cert_pubkey and probe.key_pubkey and probe.cert_pubkey != probe.key_pubkey:
        warnings.append(
            TlsWarning(
                "pair",
                "origin-cert.pem and origin-key.pem are not a pair (public keys differ)",
                (
                    "The remote `caddy validate` step will reject this and stop the",
                    "deploy before anything changes. Left in place on the server it",
                    "survives a reload but bricks the NEXT container restart, which",
                    "takes down the relay for every agent and mobile client — not",
                    "just the SPA.",
                    "Verify a pair before uploading (the hashes must match):",
                    "  openssl x509 -in origin-cert.pem -noout -pubkey | openssl sha256",
                    "  openssl pkey  -in origin-key.pem  -pubout | openssl sha256",
                ),
            )
        )
    return warnings


def format_rdn(rdn: Sequence[Sequence[Sequence[str]]]) -> str:
    """`getpeercert()`-shaped name tuples -> the `C=US, O=…` form openssl prints."""
    parts = []
    for entry in rdn or ():
        for attr in entry:
            if len(attr) == 2:
                key, value = attr
                parts.append(f"{_RDN_SHORT.get(key, key)}={value}")
    return ", ".join(parts)


def peer_cert_facts(host: str, port: int = 443, timeout: int = 10) -> tuple[str, list[str]]:
    """Issuer and SANs of whatever `host` actually serves, WITHOUT verifying it.

    `_test_decode_cert` is private CPython API (stable since 2.6) and the only
    X.509 parser in the stdlib; a failure here degrades to an openssl command
    the operator can run instead of losing the diagnosis entirely.
    """
    pem = ssl.get_server_certificate((host, port), timeout=timeout)
    handle, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(handle, "w") as fh:
            fh.write(pem)
        decoded = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
    finally:
        os.unlink(path)
    issuer = format_rdn(decoded.get("issuer", ()))
    sans = [value for kind, value in decoded.get("subjectAltName", ()) if kind == "DNS"]
    return issuer, sans


def parent_domain(host: str) -> str | None:
    """`app.example.com` -> `example.com`; None when the answer would be
    nonsense (an IP literal, or a host with no subdomain to strip). Callers
    name concrete replacement hosts only when this returns something.
    """
    labels = host.split(".")
    if len(labels) < 3 or all(label.isdigit() for label in labels):
        return None
    return ".".join(labels[1:])


def explain_server_cert(issuer: str, sans: Sequence[str], host: str) -> str:
    """Remedy text for a certificate the server is serving but nobody trusts."""
    lines = []
    if "cloudflare origin" in issuer.lower():
        covering = shadowing_sans(sans, host)
        domain = parent_domain(host)
        lines += [
            f"{host} is served a Cloudflare Origin CA certificate. Browsers do",
            "not trust the Origin CA, so this host is broken for every browser.",
        ]
        if covering:
            lines += [
                f"Caddy skipped ACME for {host} because the loaded origin cert",
                f"already matched it via SAN {covering[0]!r} (its certificate cache",
                "is global, not per-site).",
            ]
        lines.append("Fix: re-issue the Cloudflare Origin certificate with no wildcard,")
        if domain:
            lines.append(f"scoped to the proxied hosts only — {domain}, www.{domain}, api.{domain} —")
        else:
            lines.append("scoped to the proxied hosts only,")
        lines += [
            "install it on the server, then reload Caddy with --force (an",
            "in-place cert swap leaves the config identical, so a plain reload",
            'logs "config is unchanged" and does nothing).',
            "See docs/deployment.md.",
        ]
    else:
        lines += [
            f"{host} is served a certificate this machine cannot verify, and the",
            "control probe proved the local trust store is fine — so the fault is",
            "on the server, not here.",
            "Check what Caddy provisioned:",
            "  ssh mobaide 'docker compose -f /opt/mobaide/docker-compose.prod.yml"
            " logs caddy | grep -i certificate'",
            "  ssh mobaide 'ls /opt/mobaide/tls'",
        ]
    if sans:
        # repr() per name: these come from a certificate that just FAILED
        # verification, so the bytes are attacker-controlled. A dNSName is an
        # IA5String and CPython hands the bytes back verbatim, so a raw join
        # would let an embedded newline forge extra remedy lines.
        lines.append(f"Served SANs: {', '.join(repr(s) for s in sans)}")
    return "\n".join(lines)


def probe_control(
    url: str, context: ssl.SSLContext | None = None, timeout: int = CONTROL_TIMEOUT
) -> str:
    """`verified` | `untrusted` | `unreachable` for one control host.

    The three must stay distinct. Collapsing them lets an egress-filtered
    network — or an on-path attacker who simply blackholes these two hosts —
    turn a bad SERVER certificate into "this is a workstation problem".
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context):
            return "verified"
    except urllib.error.HTTPError:
        return "verified"
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        return "untrusted" if _cert_error(e) is not None else "unreachable"


def can_verify(url: str, context: ssl.SSLContext | None = None, timeout: int = CONTROL_TIMEOUT) -> bool:
    """Does this interpreter verify `url`? Any HTTP status counts as yes."""
    return probe_control(url, context, timeout) == "verified"


class ControlResult(NamedTuple):
    """`verified_url` is None unless some control host actually verified.

    `reached` records whether any of them answered at all, which is what
    separates "this interpreter trusts nothing" from "I could not find out".
    """

    verified_url: str | None
    reached: bool


def control_probe(context: ssl.SSLContext | None = None) -> ControlResult:
    """Ask the known-good hosts whether this interpreter can verify HTTPS."""
    reached = False
    for url in CONTROL_URLS:
        outcome = probe_control(url, context)
        if outcome == "verified":
            return ControlResult(url, True)
        if outcome == "untrusted":
            reached = True
    return ControlResult(None, reached)


def _cert_error(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return exc
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return reason
    return None


def diagnose(
    url: str,
    timeout: int = 15,
    *,
    context: ssl.SSLContext | None = None,
    control: ControlResult | None = None,
) -> ProbeFailure | None:
    """Why `url` is unreachable, or None when it answers.

    A caller that already ran the control probe passes the whole
    `ControlResult`. Both its fields are needed: re-probing per URL costs a
    full ladder, and a flaky network can flip the answer between URLs, so a
    per-URL verdict would blame the server under a header blaming the
    workstation.

    `timeout` bounds each network step, not the total: getaddrinfo takes no
    timeout at all, and create_connection applies the full value to every
    address a host resolves to.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if not host:
        return ProbeFailure("dns", f"{url!r} has no hostname", "Check the URL.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        socket.getaddrinfo(host, port)
    except socket.gaierror as e:
        return ProbeFailure("dns", f"{host} does not resolve ({e})", create_dns_remedy(host))

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except TimeoutError:
        return ProbeFailure(
            "timeout",
            f"{host}:{port} resolves but did not answer within {timeout}s",
            "The DNS record exists, so it is not a DNS problem. It points\n"
            "somewhere unreachable, or a firewall is dropping the packets —\n"
            "check the VPS provider firewall and `ufw status` on the server.",
        )
    except ConnectionRefusedError as e:
        return ProbeFailure(
            "connect",
            f"{host}:{port} refused the connection ({e})",
            f"The DNS record exists, so it is not a DNS problem. Nothing is\n"
            f"listening on {port}: check `docker compose ps` on the server and\n"
            f"that {port}/tcp is open in ufw.",
        )
    except OSError as e:
        # EHOSTUNREACH / ENETUNREACH and friends: the port may well have a
        # listener, so claiming "nothing is listening" would be a guess.
        return ProbeFailure(
            "connect",
            f"could not reach {host}:{port} ({e})",
            "The DNS record exists, so it is not a DNS problem. Check that the\n"
            "record points at the right address and that the host is routable.",
        )

    if context is None:
        context, _ = build_ssl_context()
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context):
            return None
    except urllib.error.HTTPError as e:
        # A Cloudflare 52x is emitted BY THE EDGE, over a perfectly valid edge
        # certificate, when the edge->origin leg fails. The handshake this
        # probe just completed therefore says nothing about the origin, and
        # treating any HTTP status as success would answer a broken origin with
        # "verified" — on an orange-clouded host, the exact incident this
        # module exists for. 525 is precisely how a mismatched origin cert/key
        # presents. These codes are Cloudflare-specific, so no ordinary origin
        # response is misread; the gray-clouded app host can never emit them.
        if e.code in (525, 526):
            return ProbeFailure(
                "tls_server",
                f"Cloudflare returned {e.code} for {host} — its TLS connection to "
                f"the origin failed (the edge certificate you just verified is not "
                f"the origin's)",
                cloudflare_origin_remedy(e.code, host),
            )
        if e.code in (521, 522, 523):
            return ProbeFailure(
                "connect",
                f"Cloudflare returned {e.code} for {host} — the edge could not "
                f"reach the origin",
                "DNS and the edge are fine; the origin is down or unreachable\n"
                "from Cloudflare. Check `docker compose ps` and `ufw status` on\n"
                "the server, and that the DNS record points at the right address.",
            )
        return None
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        cert_error = _cert_error(e)
        if cert_error is None:
            # The TCP connection already succeeded, so this is NOT necessarily
            # a handshake fault — a wedged backend behind a healthy Caddy times
            # out here too, and that is the most common deploy failure there is.
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                return ProbeFailure(
                    "timeout",
                    f"{host}:{port} accepted the connection but sent no response "
                    f"within {timeout}s",
                    "Transport and DNS are fine. Something upstream is not\n"
                    "answering: check `docker compose logs central-server` and\n"
                    "`docker compose ps` on the server.",
                )
            return ProbeFailure(
                "connect",
                f"the request to {host}:{port} failed after the TCP connection "
                f"succeeded ({e})",
                "DNS and the port are fine, so this is neither a DNS nor a\n"
                "firewall problem. Check the caddy and central-server container\n"
                "logs on the server.",
            )
        if control is None:
            control = control_probe(context)
        if control.verified_url is None:
            if not control.reached:
                # Nothing answered, so nothing was learned. Report what the
                # host serves anyway — peer_cert_facts needs no trust store,
                # and it is the only evidence available here.
                # NOT tls_local: the kind is printed verbatim ahead of the
                # detail, so labelling this "local" would contradict the very
                # sentence it introduces. Callers that suppress auto-rollback
                # on a certificate fault must treat it like the other two.
                return ProbeFailure(
                    "inconclusive",
                    "could not determine whether the fault is local: neither "
                    f"control host ({' or '.join(CONTROL_URLS)}) was reachable",
                    inconclusive_remedy(host, port, timeout),
                )
            return ProbeFailure(
                "tls_local",
                "this Python cannot verify ANY HTTPS — the control probe against "
                f"{' and '.join(CONTROL_URLS)} failed too",
                certifi_remedy(),
            )
        try:
            issuer, sans = peer_cert_facts(host, port, timeout)
        except Exception as e:
            return ProbeFailure(
                "tls_server",
                f"{host} served a certificate this machine rejects ({cert_error}); "
                f"decoding it failed ({e})",
                "Control probe passed, so the local trust store is fine and the "
                "fault is the served certificate. Inspect it with:\n"
                f"  openssl s_client -connect {host}:{port} -servername {host} "
                "</dev/null 2>/dev/null | openssl x509 -noout -issuer -ext subjectAltName",
            )
        return ProbeFailure(
            "tls_server",
            f"{host} served a cert issued by {issuer!r} with SANs {sans}",
            explain_server_cert(issuer, sans, host),
        )
