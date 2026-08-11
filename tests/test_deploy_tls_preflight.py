"""Pins on ``deploy/prod/tls_preflight.py`` and the two deploy artifacts it
guards -- the rules the failed 2026-08-10 ``app.mobaide.com`` deploy turned on.

Three independent faults produced one indistinguishable
``CERTIFICATE_VERIFY_FAILED`` and nothing named any of them:

  A. the Cloudflare Origin cert carried ``*.mobaide.com``, which Caddy's
     *global* certificate cache matched against SNI ``app.mobaide.com``,
     silently disabling ACME for that host (``san_covers_host`` is the whole
     rule; ``origin_cert_warnings`` is the guard);
  C. the workstation interpreter had no CA bundle at all, so it could not
     verify *any* HTTPS (``build_ssl_context`` + the control probe);
  D. ``caddy reload`` no-ops after an in-place cert swap, because Caddy diffs
     the adapted JSON and certs are referenced by path (``--force``).

The guard's own failure mode is pinned just as hard: a check that could not
run must never render as a check that passed. That rule is why the remote
probe emits a fact line only when it measured something, why ``OriginProbe``
carries ``sans_read``, and why every affirmative is asserted separately.

``tls_preflight`` is deliberately pure -- it returns data and never prints --
so every rule below is exercised without a server, a socket or a TTY. The
network seams (``getaddrinfo``, ``create_connection``, ``urlopen``) are
monkeypatched in the module's own namespace; nothing here touches the wire.

Lives under ``tests/`` (the repo-root test dir, NOT ``central_server/tests/``)
because this is deploy infrastructure -- the same placement as
``test_windows_launcher.py`` and ``test_install_sh.py``.
"""
from __future__ import annotations

import errno
import re
import ssl
import sys
import types
import urllib.error
from pathlib import Path
from typing import Any, Callable

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import deploy_central  # noqa: E402
import tls_preflight  # noqa: E402


# Verified against the real production artifacts.
REISSUED_SANS = ("api.mobaide.com", "mobaide.com", "www.mobaide.com")
OFFENDING_SANS = ("*.mobaide.com", "mobaide.com")
CLOUDFLARE_ORIGIN_ISSUER = (
    "C=US, O=CloudFlare, Inc., "
    "OU=CloudFlare Origin SSL Certificate Authority, "
    "L=San Francisco, ST=California"
)
# `openssl x509 -noout -ext subjectAltName` as the remote preflight sends it
# back, after its `tr '\n' ' '` squash.
OPENSSL_SAN_OUTPUT = (
    "X509v3 Subject Alternative Name:      "
    "DNS:api.mobaide.com, DNS:mobaide.com, DNS:www.mobaide.com "
)
APP_URL = "https://app.mobaide.com/"
# The apex is Proxied under CloudflareOrigin, so this is the URL whose probe
# can be answered by the Cloudflare edge rather than by the origin.
HEALTH_URL = "https://mobaide.com/api/health"


class TestSanCoversHost:
    """RFC 6125 wildcard matching -- the single rule fault A turned on.

    Caddy applies the same rule when it decides a loaded certificate already
    covers a host and skips ACME, so an over-broad answer here re-creates the
    outage and an under-broad one hides it.
    """

    @pytest.mark.parametrize(
        ("san", "host", "covered"),
        [
            ("*.mobaide.com", "app.mobaide.com", True),
            ("*.mobaide.com", "www.mobaide.com", True),
            ("*.mobaide.com", "mobaide.com", False),
            ("*.mobaide.com", "a.b.mobaide.com", False),
            ("*.other.com", "app.mobaide.com", False),
            ("mobaide.com", "mobaide.com", True),
            ("api.mobaide.com", "app.mobaide.com", False),
            ("*.MOBAIDE.com", "APP.mobaide.com", True),
            ("*.mobaide.com.", "app.mobaide.com.", True),
            ("", "app.mobaide.com", False),
        ],
    )
    def test_truth_table(self, san: str, host: str, covered: bool) -> None:
        assert tls_preflight.san_covers_host(san, host) is covered

    def test_only_the_wildcard_of_the_offending_cert_shadows_the_app_host(
        self,
    ) -> None:
        """The apex SAN shipped alongside the wildcard is innocent; naming it
        in the warning would point the operator at the wrong SAN.
        """
        assert tls_preflight.shadowing_sans(OFFENDING_SANS, "app.mobaide.com") == [
            "*.mobaide.com"
        ]

    def test_reissued_cert_shadows_nothing(self) -> None:
        assert tls_preflight.shadowing_sans(REISSUED_SANS, "app.mobaide.com") == []


class TestParentDomain:
    @pytest.mark.parametrize(
        ("host", "parent"),
        [
            ("app.mobaide.com", "mobaide.com"),
            ("a.b.mobaide.com", "b.mobaide.com"),
            ("mobaide.com", None),
            ("203.0.113.10", None),
        ],
    )
    def test_only_names_a_parent_when_the_answer_is_meaningful(
        self, host: str, parent: str | None
    ) -> None:
        assert tls_preflight.parent_domain(host) == parent


class TestParseOpensslSanOutput:
    def test_parses_the_squashed_remote_form(self) -> None:
        assert tls_preflight.parse_openssl_san_output(OPENSSL_SAN_OUTPUT) == list(
            REISSUED_SANS
        )

    def test_parses_the_raw_multiline_form(self) -> None:
        text = (
            "X509v3 Subject Alternative Name: \n"
            "    DNS:*.mobaide.com, DNS:mobaide.com\n"
        )
        assert tls_preflight.parse_openssl_san_output(text) == list(OFFENDING_SANS)

    def test_missing_extension_yields_no_names(self) -> None:
        """openssl prints nothing when the cert carries no SAN extension."""
        assert tls_preflight.parse_openssl_san_output("") == []


class TestParseOriginProbe:
    """The remote preflight emits facts on stdout; classification is local."""

    def test_reads_the_facts_out_of_a_full_preflight_stdout(self) -> None:
        stdout = [
            "USER=deploy",
            "ORIGIN_PRESENT=1",
            f"ORIGIN_SANS={OPENSSL_SAN_OUTPUT}",
            "ORIGIN_CERT_PUB=8f4e1c",
            "ORIGIN_KEY_PUB=8f4e1c",
            "OK",
        ]
        probe = tls_preflight.parse_origin_probe(stdout)
        assert probe.present is True
        assert probe.sans_read is True
        assert probe.sans == REISSUED_SANS
        assert probe.cert_pubkey == "8f4e1c"
        assert probe.key_pubkey == "8f4e1c"

    def test_present_without_facts_is_unreadable_not_clean(self) -> None:
        """The fail-open case: the PEMs exist but openssl produced nothing, so
        the probe emits no fact line at all. Reading that as "no wildcard, keys
        match" would be a false all-clear about material nothing ever parsed --
        strictly worse than having no check, because it is reassuring.
        """
        probe = tls_preflight.parse_origin_probe(
            ["USER=deploy", "ORIGIN_PRESENT=1", "OK"]
        )
        assert probe.present is True
        assert probe.sans_read is False
        assert probe.sans == ()
        assert probe.cert_pubkey == ""
        assert probe.key_pubkey == ""

    def test_a_san_extension_carrying_no_dns_name_still_counts_as_read(self) -> None:
        """``sans_read`` tracks whether openssl produced output, not whether it
        found a DNS name: an IP-only SAN genuinely cannot shadow app.<domain>,
        and downgrading it to "could not read" would nag about a healthy cert.
        """
        probe = tls_preflight.parse_origin_probe(
            [
                "ORIGIN_PRESENT=1",
                "ORIGIN_SANS=X509v3 Subject Alternative Name: "
                "IP Address:203.0.113.10 ",
            ]
        )
        assert probe.sans_read is True
        assert probe.sans == ()

    def test_missing_cert_is_reported_as_absent_not_as_empty_sans(self) -> None:
        probe = tls_preflight.parse_origin_probe(
            ["USER=deploy", "ORIGIN_CERT_MISSING=1", "OK"]
        )
        assert probe.present is False
        assert probe.sans_read is False
        assert probe.sans == ()


class TestOriginCertWarnings:
    """``kind`` is the field callers branch on.

    ``remote_preflight`` decides which affirmative it is still entitled to
    print from ``{w.kind for w in warnings}``, so matching on prose here would
    make a copy-edit silently re-enable a false all-clear.
    """

    def test_reissued_production_cert_is_clean(self) -> None:
        """The re-issued cert now on the server: no wildcard, matching pair."""
        probe = tls_preflight.OriginProbe(
            True, REISSUED_SANS, "8f4e1c", "8f4e1c", True
        )
        assert tls_preflight.origin_cert_warnings(probe, "mobaide.com") == []

    def test_wildcard_san_warns_and_names_the_replacement_hosts(self) -> None:
        probe = tls_preflight.OriginProbe(
            True, OFFENDING_SANS, "8f4e1c", "8f4e1c", True
        )
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == ["shadow"]
        assert "'*.mobaide.com'" in warnings[0].headline
        assert "app.mobaide.com" in warnings[0].headline
        body = "\n".join(warnings[0].lines)
        assert "mobaide.com, www.mobaide.com, api.mobaide.com" in body
        assert "--force" in body

    def test_an_uncovered_proxied_host_is_a_third_kind_of_finding(self) -> None:
        """The SAN list is checked in both directions, because the runbook now
        asks the operator to hand-edit it in the dashboard. Over-coverage
        breaks the SPA; a name dropped from it is a 526 for that host under
        Full (strict), and api.<domain> carries every agent tunnel and mobile
        client rather than a browser someone would notice.
        """
        probe = tls_preflight.OriginProbe(
            True, ("mobaide.com", "www.mobaide.com"), "8f4e1c", "8f4e1c", True
        )
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == ["missing"]
        assert "api.mobaide.com" in warnings[0].headline
        assert "www.mobaide.com" not in warnings[0].headline
        body = "\n".join(warnings[0].lines)
        assert "mobaide.com, www.mobaide.com, api.mobaide.com" in body

    def test_every_uncovered_host_lands_in_one_finding(self) -> None:
        """Callers branch on the *set* of kinds and print one block per
        warning, so an apex-only cert has to name both survivors in one place.
        """
        probe = tls_preflight.OriginProbe(
            True, ("mobaide.com",), "8f4e1c", "8f4e1c", True
        )
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == ["missing"]
        assert "www.mobaide.com" in warnings[0].headline
        assert "api.mobaide.com" in warnings[0].headline

    def test_a_wildcard_only_cert_is_over_and_under_covered_at_once(self) -> None:
        """A wildcard spans exactly one label: `*.mobaide.com` does cover the
        www and api hosts, so neither is "missing" -- but it leaves the apex,
        served from this same file, uncovered while still shadowing the SPA
        host. Deleting the apex SAN is one keystroke in that dashboard edit.
        """
        probe = tls_preflight.OriginProbe(
            True, ("*.mobaide.com",), "8f4e1c", "8f4e1c", True
        )
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == ["shadow", "missing"]
        assert "www." not in warnings[1].headline
        assert "api." not in warnings[1].headline

    @pytest.mark.parametrize(
        ("sans_read", "kinds"),
        [(True, ["missing"]), (False, [])],
        ids=["read-and-empty", "never-read"],
    )
    def test_only_a_san_list_that_was_read_can_prove_a_host_uncovered(
        self, sans_read: bool, kinds: list[str]
    ) -> None:
        """Both cases arrive as an empty ``sans``. "Not in the list" is not
        evidence when there is no list: warning there sends the operator to
        re-issue a certificate that may be perfectly correct, on the strength
        of a missing openssl.
        """
        probe = tls_preflight.OriginProbe(True, (), "8f4e1c", "8f4e1c", sans_read)
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == kinds

    def test_mismatched_public_keys_warn_as_a_separate_finding(self) -> None:
        probe = tls_preflight.OriginProbe(
            True, REISSUED_SANS, "8f4e1c", "b02d99", True
        )
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == ["pair"]
        assert "not a pair" in warnings[0].headline

    def test_both_faults_at_once_stay_two_distinguishable_findings(self) -> None:
        probe = tls_preflight.OriginProbe(
            True, OFFENDING_SANS, "8f4e1c", "b02d99", True
        )
        warnings = tls_preflight.origin_cert_warnings(probe, "mobaide.com")
        assert [w.kind for w in warnings] == ["shadow", "pair"]

    def test_unreadable_hashes_do_not_claim_a_mismatch(self) -> None:
        """openssl failing on the server leaves both fields empty -- which is
        "unknown", not "different"; a false pair warning would send the
        operator to re-upload a healthy key.
        """
        probe = tls_preflight.OriginProbe(True, REISSUED_SANS, "", "", True)
        assert tls_preflight.origin_cert_warnings(probe, "mobaide.com") == []

    def test_absent_cert_reports_nothing(self) -> None:
        probe = tls_preflight.OriginProbe(False, (), "", "", False)
        assert tls_preflight.origin_cert_warnings(probe, "mobaide.com") == []


class TestExplainServerCert:
    def test_real_issuer_rdn_renders_the_string_the_special_case_matches(
        self,
    ) -> None:
        """``format_rdn`` feeds ``explain_server_cert``: if the rendering drifts,
        the Cloudflare special case below silently stops firing.
        """
        issuer = tls_preflight.format_rdn(
            (
                (("countryName", "US"),),
                (("organizationName", "CloudFlare, Inc."),),
                (
                    (
                        "organizationalUnitName",
                        "CloudFlare Origin SSL Certificate Authority",
                    ),
                ),
                (("localityName", "San Francisco"),),
                (("stateOrProvinceName", "California"),),
            )
        )
        assert issuer == CLOUDFLARE_ORIGIN_ISSUER

    def test_cloudflare_origin_issuer_explains_the_acme_shadow(self) -> None:
        text = tls_preflight.explain_server_cert(
            CLOUDFLARE_ORIGIN_ISSUER, OFFENDING_SANS, "app.mobaide.com"
        )
        assert "Cloudflare Origin CA certificate" in text
        assert "skipped ACME for app.mobaide.com" in text
        assert "no wildcard" in text
        assert "mobaide.com, www.mobaide.com, api.mobaide.com" in text

    def test_unknown_issuer_does_not_invent_the_cloudflare_diagnosis(self) -> None:
        text = tls_preflight.explain_server_cert(
            "C=US, O=Let's Encrypt, CN=E7", ["app.mobaide.com"], "app.mobaide.com"
        )
        assert "Cloudflare" not in text
        assert "the fault is" in text
        assert "Served SANs: 'app.mobaide.com'" in text

    def test_served_sans_cannot_forge_remedy_lines_or_repaint_the_verdict(
        self,
    ) -> None:
        """These names come off a certificate that just FAILED verification, so
        the bytes are attacker-controlled: a dNSName is an IA5String and CPython
        hands it back verbatim. Joined raw, an embedded newline forges extra
        remedy lines and an embedded ESC repaints the verdict printed above.
        """
        hostile = "ok.example\n  ssh mobaide 'rm -rf /opt/mobaide'\x1b[2J"
        text = tls_preflight.explain_server_cert(
            "C=US, O=Unknown", [hostile], "app.mobaide.com"
        )
        lines = text.splitlines()
        assert len([ln for ln in lines if ln.startswith("Served SANs:")]) == 1
        assert lines[-1].startswith("Served SANs:")
        assert "\x1b" not in text
        assert "\\x1b" in lines[-1]
        assert "\\n" in lines[-1]


CERTIFI_PATH = "/fake/certifi/cacert.pem"


def _verify_paths(cafile: str | None, capath: str | None) -> ssl.DefaultVerifyPaths:
    return ssl.DefaultVerifyPaths(
        cafile=cafile,
        capath=capath,
        openssl_cafile_env="SSL_CERT_FILE",
        openssl_cafile="/opt/python/etc/openssl/cert.pem",
        openssl_capath_env="SSL_CERT_DIR",
        openssl_capath="/opt/python/etc/openssl/certs",
    )


def _explodes(what: str) -> Callable[..., Any]:
    def fake(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{what} must not be reached here")

    return fake


def _install_stock_context(
    monkeypatch: pytest.MonkeyPatch, *, trusts: bool
) -> tuple[ssl.SSLContext, dict[str, Any]]:
    """Pin what a stock ``ssl.create_default_context()`` reports.

    ``build_ssl_context`` calls that constructor for two different reasons:
    bare, to probe whether a default context trusts anything at all, and with
    ``cafile=`` to build the certifi fallback. ``trusts`` answers the probe;
    the returned dict captures the fallback call's kwargs.
    """
    sentinel = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    seen: dict[str, Any] = {}
    roots = [{"subject": ((("commonName", "Fake Root CA"),),)}] if trusts else []

    def fake(*args: Any, **kwargs: Any) -> Any:
        if not args and not kwargs:
            return types.SimpleNamespace(get_ca_certs=lambda: roots)
        seen.update(kwargs)
        return sentinel

    monkeypatch.setattr(tls_preflight.ssl, "create_default_context", fake)
    return sentinel, seen


class TestBuildSslContext:
    """Fault C: a python.org build with no CA bundle verifies nothing at all.

    The verdict comes from PROBING a stock context, not from reading
    ``get_default_verify_paths()``. Windows reports no cafile and no capath there while
    verifying perfectly off the ROOT/CA stores, so inferring would swap the OS trust
    store for certifi on every Windows deploy.
    """

    @pytest.fixture(autouse=True)
    def _no_ssl_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Either variable short-circuits the whole function, so the host's own
        environment would otherwise decide the verdict.
        """
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    def test_healthy_interpreter_gets_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stock_context(monkeypatch, trusts=True)
        monkeypatch.setitem(
            sys.modules, "certifi", types.SimpleNamespace(where=_explodes("certifi"))
        )
        assert tls_preflight.build_ssl_context() == (None, None)

    def test_windows_shaped_verify_paths_do_not_trigger_the_swap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cafile and capath both None -- exactly what Windows reports -- while
        the stock context is fully loaded. Reading the paths instead of probing
        condemns every Windows deploy to certifi.
        """
        monkeypatch.setattr(
            tls_preflight.ssl,
            "get_default_verify_paths",
            lambda: _verify_paths(None, None),
        )
        _install_stock_context(monkeypatch, trusts=True)
        monkeypatch.setitem(
            sys.modules, "certifi", types.SimpleNamespace(where=_explodes("certifi"))
        )
        assert tls_preflight.build_ssl_context() == (None, None)

    def test_a_populated_capath_counts_even_when_get_ca_certs_is_lazy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CPython loads a capath directory lazily, so a hashed capath with
        real roots in it still reports ``get_ca_certs() == []`` until one is
        used -- reading that as "trusts nothing" would swap a populated OS
        trust store for certifi.
        """
        (tmp_path / "abcd1234.0").write_bytes(b"fake rehashed root")
        monkeypatch.setattr(
            tls_preflight.ssl,
            "get_default_verify_paths",
            lambda: _verify_paths(None, str(tmp_path)),
        )
        _install_stock_context(monkeypatch, trusts=False)
        monkeypatch.setitem(
            sys.modules, "certifi", types.SimpleNamespace(where=_explodes("certifi"))
        )
        assert tls_preflight.build_ssl_context() == (None, None)

    @pytest.mark.parametrize(
        ("platform", "expected", "forbidden"),
        [
            ("darwin", "Install Certificates.command", "platform's CA bundle"),
            ("win32", "platform's CA bundle", "Install Certificates.command"),
        ],
    )
    def test_falls_back_to_certifi_when_the_stock_context_trusts_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        expected: str,
        forbidden: str,
    ) -> None:
        monkeypatch.setattr(tls_preflight.sys, "platform", platform)
        monkeypatch.setattr(
            tls_preflight.ssl,
            "get_default_verify_paths",
            lambda: _verify_paths(None, None),
        )
        monkeypatch.setitem(
            sys.modules, "certifi", types.SimpleNamespace(where=lambda: CERTIFI_PATH)
        )
        sentinel, seen = _install_stock_context(monkeypatch, trusts=False)

        context, warning = tls_preflight.build_ssl_context()

        assert context is sentinel
        assert seen["cafile"] == CERTIFI_PATH
        assert warning is not None
        assert "/opt/python/etc/openssl/cert.pem" in warning
        assert expected in warning
        assert forbidden not in warning

    @pytest.mark.parametrize("var", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
    def test_explicit_env_override_is_left_alone_even_when_broken(
        self, monkeypatch: pytest.MonkeyPatch, var: str
    ) -> None:
        """A deliberate override wins over the fallback: silently routing around
        it would hide the operator's own mistake. Exploding both later steps
        pins the ORDER too -- the env check has to short-circuit before the
        probe, whose answer is irrelevant once a store was named by hand.
        """
        monkeypatch.setenv(var, "/does/not/exist")
        monkeypatch.setattr(
            tls_preflight.ssl, "create_default_context", _explodes("the trust probe")
        )
        monkeypatch.setitem(
            sys.modules, "certifi", types.SimpleNamespace(where=_explodes("certifi"))
        )
        assert tls_preflight.build_ssl_context() == (None, None)

    def test_absent_certifi_leaves_urllib_defaults_in_place(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stock_context(monkeypatch, trusts=False)
        # `None` in sys.modules is how CPython spells "this import must fail".
        monkeypatch.setitem(sys.modules, "certifi", None)
        assert tls_preflight.build_ssl_context() == (None, None)

    def test_an_unusable_context_constructor_does_not_trigger_a_certifi_swap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe is a live call into OpenSSL; if it raises, that is not
        POSITIVE evidence the store is untrusted -- only positive evidence is
        allowed to swap the operator's own trust store for certifi, so an
        unusable constructor has to leave it alone instead.
        """
        calls: list[dict[str, Any]] = []

        def fake(*args: Any, **kwargs: Any) -> ssl.SSLContext:
            if not args and not kwargs:
                raise ssl.SSLError("cannot load default verify locations")
            calls.append(kwargs)
            return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        monkeypatch.setattr(tls_preflight.ssl, "create_default_context", fake)
        monkeypatch.setitem(
            sys.modules, "certifi", types.SimpleNamespace(where=lambda: CERTIFI_PATH)
        )
        assert tls_preflight.build_ssl_context() == (None, None)
        assert calls == []


class TestCertifiRemedy:
    """The remedy text is platform-aware for the same reason the probe is."""

    @pytest.mark.parametrize(
        ("platform", "expected", "forbidden"),
        [
            ("darwin", "Install Certificates.command", "platform's CA bundle"),
            ("win32", "platform's CA bundle", "Install Certificates.command"),
            ("linux", "platform's CA bundle", "Install Certificates.command"),
        ],
    )
    def test_the_permanent_fix_names_only_a_file_this_platform_has(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        expected: str,
        forbidden: str,
    ) -> None:
        monkeypatch.setattr(tls_preflight.sys, "platform", platform)
        remedy = tls_preflight.certifi_remedy()
        assert expected in remedy
        assert forbidden not in remedy

    def test_every_platform_keeps_the_two_load_bearing_facts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whose fault it is (so nobody restarts a healthy server), and that
        certifi will NOT rescue an origin-cert host (it carries no Cloudflare
        Origin CA), so the fallback is not mistaken for a fix for fault A.
        """
        monkeypatch.setattr(tls_preflight.sys, "platform", "win32")
        remedy = tls_preflight.certifi_remedy()
        assert "workstation problem, not a server problem" in remedy
        assert "does NOT contain the Cloudflare Origin CA" in remedy


class TestCloudflareOriginRemedy:
    """Two codes, two unrelated mechanisms -- and the operator acts on the
    mechanism, not on the number.
    """

    def test_525_names_the_mismatched_pair_and_its_delayed_fuse(self) -> None:
        remedy = tls_preflight.cloudflare_origin_remedy(525, "mobaide.com")
        assert "do not match" in remedy
        assert "NEXT container restart" in remedy
        assert "-servername mobaide.com" in remedy
        assert "Full (strict)" not in remedy

    def test_526_names_the_strict_mode_rejection_not_a_key_mismatch(self) -> None:
        """Cloudflare accepted the handshake here, so re-uploading a matching
        pair fixes nothing: the cert is expired or does not carry this name.
        """
        remedy = tls_preflight.cloudflare_origin_remedy(526, "mobaide.com")
        assert "Full (strict)" in remedy
        assert "SANs" in remedy
        assert "do not match" not in remedy


class TestCreateDnsRemedy:
    """The gray-cloud instruction is right for exactly one host.

    app.<domain> is DNS-only in both TLS modes. The apex and api records are the
    opposite under CloudflareOrigin: Proxied, because the origin cert is only trusted by
    Cloudflare. Graying those out is how you take the relay off Cloudflare mid-incident,
    so blanket advice here is actively dangerous.
    """

    def test_the_spa_host_is_gray_cloud_in_both_modes(self) -> None:
        remedy = tls_preflight.create_dns_remedy("app.mobaide.com")
        assert "DNS only (gray cloud) in BOTH TLS modes" in remedy
        assert "Proxied" not in remedy

    @pytest.mark.parametrize("host", ["mobaide.com", "api.mobaide.com"])
    def test_proxied_hosts_are_never_told_to_go_gray(self, host: str) -> None:
        remedy = tls_preflight.create_dns_remedy(host)
        assert host in remedy
        assert "Proxied (orange cloud)" in remedy
        assert "BOTH TLS modes" not in remedy


class _FakeResponse:
    """Stand-in for whatever urlopen/create_connection hands back."""

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _resolves(*args: Any, **kwargs: Any) -> list[tuple[str, str]]:
    return [("AF_INET", "SOCK_STREAM")]


def _connects(*args: Any, **kwargs: Any) -> _FakeResponse:
    return _FakeResponse()


def _cert_verification_failure() -> urllib.error.URLError:
    """What urlopen raises for both fault A and fault C -- indistinguishable."""
    return urllib.error.URLError(
        ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate (_ssl.c:1028)"
        )
    )


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"status {code}", {}, None)  # type: ignore[arg-type]


def _urlopen(target: BaseException | None, *, control_verifies: bool) -> Callable[..., Any]:
    """urlopen fake that answers control hosts and the target differently."""

    def fake(url: str, *args: Any, **kwargs: Any) -> _FakeResponse:
        if url in tls_preflight.CONTROL_URLS:
            if control_verifies:
                return _FakeResponse()
            raise urllib.error.URLError("control host unreachable")
        if target is not None:
            raise target
        return _FakeResponse()

    return fake


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    urlopen: Callable[..., Any],
    resolve: Callable[..., Any] = _resolves,
    connect: Callable[..., Any] = _connects,
) -> None:
    """Patch the three seams `diagnose` reaches the network through.

    ``build_ssl_context`` is pinned too so the verdict does not depend on
    whichever CA store the machine running the suite happens to have.
    """
    monkeypatch.setattr(tls_preflight, "build_ssl_context", lambda: (None, None))
    monkeypatch.setattr(tls_preflight.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(tls_preflight.socket, "create_connection", connect)
    monkeypatch.setattr(tls_preflight.urllib.request, "urlopen", urlopen)


class TestDiagnoseTransport:
    """Each verdict is only reachable once the previous step succeeded -- that
    ordering is what makes "create the DNS record" trustworthy advice.
    """

    def test_unresolvable_host_is_a_dns_fault(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def gaierror(*args: Any, **kwargs: Any) -> None:
            raise tls_preflight.socket.gaierror(-2, "Name or service not known")

        _install_transport(
            monkeypatch, resolve=gaierror, urlopen=_urlopen(None, control_verifies=True)
        )
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "dns"
        assert "app.mobaide.com" in failure.detail
        assert "DNS only (gray cloud)" in failure.remedy

    def test_refused_connection_is_not_reported_as_dns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refused(*args: Any, **kwargs: Any) -> None:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

        _install_transport(
            monkeypatch, connect=refused, urlopen=_urlopen(None, control_verifies=True)
        )
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "connect"
        assert "refused the connection" in failure.detail
        assert "not a DNS problem" in failure.remedy
        assert "listening on 443" in failure.remedy

    def test_an_unroutable_host_does_not_claim_nothing_is_listening(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EHOSTUNREACH and friends never reached a port at all, so a listener
        may well be running. "Nothing is listening" there sends the operator to
        `docker compose ps` for a routing or DNS-target fault.
        """

        def unreachable(*args: Any, **kwargs: Any) -> None:
            raise OSError(errno.EHOSTUNREACH, "No route to host")

        _install_transport(
            monkeypatch,
            connect=unreachable,
            urlopen=_urlopen(None, control_verifies=True),
        )
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "connect"
        assert "could not reach app.mobaide.com:443" in failure.detail
        assert "listening" not in failure.remedy
        assert "routable" in failure.remedy

    def test_silent_host_is_a_timeout_not_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TimeoutError is an OSError subclass, so the two handlers' order is
        load-bearing: a firewall dropping SYNs must not read as "nothing is
        listening on 443".
        """

        def times_out(*args: Any, **kwargs: Any) -> None:
            raise TimeoutError("timed out")

        _install_transport(
            monkeypatch, connect=times_out, urlopen=_urlopen(None, control_verifies=True)
        )
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "timeout"
        assert "firewall" in failure.remedy

    def test_reachable_host_reports_no_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_transport(monkeypatch, urlopen=_urlopen(None, control_verifies=True))
        assert tls_preflight.diagnose(APP_URL) is None

    def test_http_error_status_still_counts_as_reachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The app webroot 404s before the first web deploy; that proves the
        site block and the certificate are fine.
        """
        not_found = urllib.error.HTTPError(APP_URL, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        _install_transport(
            monkeypatch, urlopen=_urlopen(not_found, control_verifies=True)
        )
        assert tls_preflight.diagnose(APP_URL) is None

    def test_a_post_connect_failure_is_not_labelled_a_handshake_fault(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The TCP connection already succeeded, so neither "handshake" nor
        "nothing is listening" is a fact in evidence -- and both send the
        operator somewhere the fault is not.
        """
        broken = urllib.error.URLError(ssl.SSLError(1, "record layer failure"))
        _install_transport(monkeypatch, urlopen=_urlopen(broken, control_verifies=True))
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "connect"
        assert "after the TCP connection succeeded" in failure.detail
        assert "handshake" not in failure.detail.lower()
        assert "listening" not in failure.remedy
        assert "central-server" in failure.remedy

    @pytest.mark.parametrize(
        "raised",
        [
            TimeoutError("timed out"),
            urllib.error.URLError(TimeoutError("timed out")),
        ],
        ids=["raw", "wrapped-in-urlerror"],
    )
    def test_post_connect_silence_blames_the_backend_not_the_certificate(
        self, monkeypatch: pytest.MonkeyPatch, raised: BaseException
    ) -> None:
        """A wedged backend behind a healthy Caddy is the most common deploy
        failure there is, and it surfaces here -- after the connection lands.
        Reporting it as a TLS fault costs an operator the whole investigation.
        """
        _install_transport(monkeypatch, urlopen=_urlopen(raised, control_verifies=True))
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "timeout"
        assert "accepted the connection" in failure.detail
        assert "docker compose logs central-server" in failure.remedy
        assert "certificate" not in failure.remedy.lower()


class TestCloudflareEdgeStatuses:
    """A 52x is emitted BY THE EDGE, over a valid edge certificate.

    The handshake this probe just completed therefore says nothing about the
    origin behind it, so the "any status means reachable" rule -- correct for
    an origin response -- reported a broken origin as verified on every
    orange-clouded host. 525 is exactly how the mismatched cert/key this
    module warns about presents.
    """

    @pytest.mark.parametrize("code", [525, 526])
    def test_a_failed_origin_handshake_is_a_server_certificate_verdict(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        _install_transport(
            monkeypatch,
            urlopen=_urlopen(_http_error(HEALTH_URL, code), control_verifies=True),
        )
        failure = tls_preflight.diagnose(HEALTH_URL)
        assert failure is not None
        assert failure.kind == "tls_server"
        assert str(code) in failure.detail
        assert "mobaide.com" in failure.detail
        assert "-servername mobaide.com" in failure.remedy

    @pytest.mark.parametrize("code", [521, 522, 523])
    def test_an_origin_the_edge_cannot_reach_is_not_a_certificate_verdict(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        """Nothing answered the edge at all, so no certificate was involved --
        and ``tls_server`` is the class that suppresses the auto-rollback.
        """
        _install_transport(
            monkeypatch,
            urlopen=_urlopen(_http_error(HEALTH_URL, code), control_verifies=True),
        )
        failure = tls_preflight.diagnose(HEALTH_URL)
        assert failure is not None
        assert failure.kind == "connect"
        assert str(code) in failure.detail
        assert "docker compose ps" in failure.remedy
        assert "certificate" not in failure.remedy.lower()

    @pytest.mark.parametrize("code", [404, 500])
    def test_an_ordinary_origin_response_still_counts_as_reachable(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        """Only Cloudflare's own range is edge-emitted. A 404 (the webroot
        before the first web deploy) or a 500 from the app itself proves the
        whole chain terminated at the origin, which is what this probe asks.
        """
        _install_transport(
            monkeypatch,
            urlopen=_urlopen(_http_error(HEALTH_URL, code), control_verifies=True),
        )
        assert tls_preflight.diagnose(HEALTH_URL) is None


class TestProbeControl:
    """Only a certificate failure is evidence about the trust store. Collapsing an
    unreachable host into the same verdict as an untrusted one is what let an on-path
    attacker blackhole the two control hosts to manufacture a false verdict about the
    workstation instead of the server.
    """

    def test_a_certificate_failure_is_untrusted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            tls_preflight.urllib.request,
            "urlopen",
            _urlopen(_cert_verification_failure(), control_verifies=True),
        )
        assert tls_preflight.probe_control(APP_URL) == "untrusted"

    def test_a_blocked_connection_is_unreachable_not_untrusted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            tls_preflight.urllib.request,
            "urlopen",
            _urlopen(urllib.error.URLError("blocked"), control_verifies=True),
        )
        assert tls_preflight.probe_control(APP_URL) == "unreachable"


class TestDiagnoseControlProbe:
    """The one case the whole module exists for.

    A broken workstation trust store (fault C) and a bad server certificate
    (fault A) raise the *same* CERTIFICATE_VERIFY_FAILED. Only the control
    probe against a known-good public host tells them apart, and getting it
    backwards sends the operator to fix the wrong machine.
    """

    def test_no_control_host_reachable_is_reported_as_inconclusive_not_local_blame(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Neither control host answering means nothing was learned about the
        trust store. The verdict has to stay unresolved instead.
        """
        _install_transport(
            monkeypatch,
            urlopen=_urlopen(_cert_verification_failure(), control_verifies=False),
        )
        monkeypatch.setattr(
            tls_preflight,
            "peer_cert_facts",
            lambda *args, **kwargs: (CLOUDFLARE_ORIGIN_ISSUER, list(OFFENDING_SANS)),
        )
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "tls_local"
        assert "INCONCLUSIVE" in failure.remedy
        assert "workstation problem, not a server problem" not in failure.remedy

    def test_control_probe_passing_blames_the_served_certificate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_transport(
            monkeypatch,
            urlopen=_urlopen(_cert_verification_failure(), control_verifies=True),
        )
        monkeypatch.setattr(
            tls_preflight,
            "peer_cert_facts",
            lambda *args, **kwargs: (CLOUDFLARE_ORIGIN_ISSUER, list(OFFENDING_SANS)),
        )
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "tls_server"
        assert "CloudFlare Origin SSL Certificate Authority" in failure.detail
        assert "skipped ACME for app.mobaide.com" in failure.remedy
        assert "pip install certifi" not in failure.remedy

    def test_undecodable_certificate_keeps_the_verdict_and_hands_over_openssl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``peer_cert_facts`` leans on private CPython API. If it ever breaks,
        the classification must survive -- only the detail degrades.
        """

        def boom(*args: Any, **kwargs: Any) -> None:
            raise AttributeError("module 'ssl' has no attribute '_ssl'")

        _install_transport(
            monkeypatch,
            urlopen=_urlopen(_cert_verification_failure(), control_verifies=True),
        )
        monkeypatch.setattr(tls_preflight, "peer_cert_facts", boom)
        failure = tls_preflight.diagnose(APP_URL)
        assert failure is not None
        assert failure.kind == "tls_server"
        assert "openssl s_client" in failure.remedy

    @pytest.mark.parametrize(
        ("control_verified", "kind"),
        [(True, "tls_server"), (False, "tls_local")],
    )
    def test_a_caller_supplied_verdict_skips_the_probe_entirely(
        self, monkeypatch: pytest.MonkeyPatch, control_verified: bool, kind: str
    ) -> None:
        """The urlopen fake answers control hosts the OPPOSITE way, so a verdict
        matching the flag can only have come from the flag.
        """
        _install_transport(
            monkeypatch,
            urlopen=_urlopen(
                _cert_verification_failure(), control_verifies=not control_verified
            ),
        )
        monkeypatch.setattr(
            tls_preflight, "control_probe", _explodes("the control probe")
        )
        monkeypatch.setattr(
            tls_preflight,
            "peer_cert_facts",
            lambda *args, **kwargs: (CLOUDFLARE_ORIGIN_ISSUER, list(OFFENDING_SANS)),
        )
        failure = tls_preflight.diagnose(APP_URL, control_verified=control_verified)
        assert failure is not None
        assert failure.kind == kind

    def test_one_blocked_control_host_does_not_condemn_the_trust_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A network that blocks a single public host must not be reported as
        "your CA store is broken" -- hence more than one control URL.
        """
        first, second = tls_preflight.CONTROL_URLS[0], tls_preflight.CONTROL_URLS[1]

        def fake(url: str, *args: Any, **kwargs: Any) -> _FakeResponse:
            if url == first:
                raise urllib.error.URLError("blocked")
            return _FakeResponse()

        monkeypatch.setattr(tls_preflight.urllib.request, "urlopen", fake)
        assert tls_preflight.control_probe().verified_url == second


class TestDeployCommandInvariants:
    def test_cloudflare_origin_forces_the_caddy_reload(self) -> None:
        """Fault D: certs are referenced by path, so an in-place PEM swap leaves
        the adapted JSON identical and a plain reload logs "config is
        unchanged" -- reading like success while the old cert stays resident.
        """
        cmd = deploy_central.build_deploy_cmd("CloudflareOrigin", skip_build=False)
        assert "caddy reload --force --config /etc/caddy/Caddyfile" in cmd

    def test_letsencrypt_reload_is_not_forced(self) -> None:
        """LetsEncrypt's Caddyfile references no certificate by path at all (see
        ``test_letsencrypt_mode_loads_no_certificate_at_all``), so forcing here
        buys nothing and charges that disconnect to every no-op deploy.
        """
        cmd = deploy_central.build_deploy_cmd("LetsEncrypt", skip_build=False)
        assert "caddy reload --config /etc/caddy/Caddyfile" in cmd
        assert "--force" not in cmd

    def test_origin_cert_probe_runs_only_in_cloudflare_origin_mode(self) -> None:
        cloudflare = deploy_central.build_preflight_script("CloudflareOrigin")
        letsencrypt = deploy_central.build_preflight_script("LetsEncrypt")
        for marker in (
            "ORIGIN_PRESENT=1",
            "ORIGIN_SANS=",
            "ORIGIN_CERT_PUB=",
            "ORIGIN_KEY_PUB=",
            "ORIGIN_CERT_MISSING=1",
        ):
            assert marker in cloudflare
            assert marker not in letsencrypt

    def test_origin_cert_probe_reads_the_paths_caddy_is_given(self) -> None:
        cloudflare = deploy_central.build_preflight_script("CloudflareOrigin")
        assert deploy_central.ORIGIN_CERT in cloudflare
        assert deploy_central.ORIGIN_KEY in cloudflare

    def test_origin_cert_probe_cannot_fail_the_preflight(self) -> None:
        """Warn, never fail (a wildcard cert only breaks the SPA, so it must not
        block a central-server deploy). Appending to ``$errs`` here would turn
        the finding into a hard stop.
        """
        assert "errs" not in deploy_central._ORIGIN_CERT_PROBE

    @pytest.mark.parametrize("tls_mode", ["LetsEncrypt", "CloudflareOrigin"])
    def test_shared_prereq_checks_survive_in_both_modes(self, tls_mode: str) -> None:
        script = deploy_central.build_preflight_script(tls_mode)
        for marker in ("BASE_NOT_WRITABLE", "ENV_MISSING", "DOCKER", 'echo "OK"'):
            assert marker in script


class TestOriginCertProbeScript:
    """The fail-open rule, enforced on the shell side.

    Piping a FAILED ``openssl`` straight into ``sha256`` hashes EMPTY input, and every
    empty input hashes to the same e3b0c442... -- so two unreadable PEMs compared EQUAL
    and sailed through the pair check. The preflight then printed an affirmative all-
    clear about material it had never parsed.
    """

    def test_no_openssl_output_is_piped_straight_into_sha256(self) -> None:
        assert (
            re.search(
                r"openssl (x509|pkey)[^|\n]*\|\s*openssl sha256",
                deploy_central._ORIGIN_CERT_PROBE,
            )
            is None
        )

    def test_both_hashes_are_taken_of_a_captured_non_empty_variable(self) -> None:
        hashed = [
            line
            for line in deploy_central._ORIGIN_CERT_PROBE.splitlines()
            if "openssl sha256" in line
        ]
        assert len(hashed) == 2
        for line in hashed:
            assert line.lstrip().startswith('[ -n "$')
            assert "printf '%s' \"$" in line

    @pytest.mark.parametrize(
        "var", ["origin_sans", "origin_cert_pub", "origin_key_pub"]
    )
    def test_each_fact_is_echoed_only_when_it_was_actually_measured(
        self, var: str
    ) -> None:
        assert f'[ -n "${var}" ] && echo' in deploy_central._ORIGIN_CERT_PROBE

    def test_presence_is_reported_before_any_openssl_can_fail(self) -> None:
        """ORIGIN_PRESENT=1 is the line that tells the caller the PEMs exist
        while every openssl fact is missing -- the state that has to read as
        "the check did NOT run", not as "clean".
        """
        probe = deploy_central._ORIGIN_CERT_PROBE
        assert probe.index('echo "ORIGIN_PRESENT=1"') < probe.index("openssl")

    def test_an_encrypted_key_fails_fast_instead_of_prompting(self) -> None:
        """Without an empty -passin, ``openssl pkey`` on an encrypted key blocks
        on a passphrase prompt. Inside an ssh round-trip with no TTY that hangs
        the whole preflight instead of degrading to "could not derive".
        """
        assert "-passin pass:" in deploy_central._ORIGIN_CERT_PROBE


_TLS_MODES = ("LetsEncrypt", "CloudflareOrigin")

_SITE_BLOCK_RE = re.compile(
    r"^(?P<header>[^\s#{][^\n{]*?)\s*\{\n(?P<body>.*?)^\}",
    re.DOTALL | re.MULTILINE,
)


def _caddyfile(tls_mode: str) -> dict[str, str]:
    """Top-level Caddy blocks (site blocks and snippets) as header -> body."""
    path = deploy_central.DEPLOY_DIR / f"Caddyfile.{tls_mode}"
    assert path.is_file(), f"missing Caddyfile: {path}"
    text = path.read_text(encoding="utf-8")
    return {
        m.group("header").strip(): m.group("body")
        for m in _SITE_BLOCK_RE.finditer(text)
    }


def _directives(body: str) -> list[str]:
    return [
        line.strip().split()[0]
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class TestCaddyfileAcmePrecondition:
    """The structural condition the app host's ACME cert depends on.

    ``app.mobaide.com`` has no ``tls`` directive by design: Caddy issues its certificate
    over HTTP-01. That only happens while no *loaded* certificate matches the host, so
    the origin cert must stay off both this block and the snippet it imports.
    """

    @pytest.mark.parametrize("tls_mode", _TLS_MODES)
    def test_app_host_loads_no_certificate_of_its_own(self, tls_mode: str) -> None:
        blocks = _caddyfile(tls_mode)
        assert "app.mobaide.com" in blocks
        assert "tls" not in _directives(blocks["app.mobaide.com"])
        assert "import app_mobaide" in blocks["app.mobaide.com"]
        assert "tls" not in _directives(blocks["(app_mobaide)"])

    def test_origin_cert_is_loaded_only_by_the_proxied_hosts(self) -> None:
        blocks = _caddyfile("CloudflareOrigin")
        loaders = sorted(
            header for header, body in blocks.items() if "tls" in _directives(body)
        )
        assert loaders == ["mobaide.com, api.mobaide.com", "www.mobaide.com"]

    def test_the_san_check_covers_exactly_the_hosts_that_load_the_cert(self) -> None:
        """``proxied_hosts`` is the list the ``missing`` finding is computed
        from, so a site block gained or dropped here silently changes what that
        check can see -- and an unchecked host is a 526 for every client of it.
        """
        blocks = _caddyfile("CloudflareOrigin")
        loaded_by = {
            host.strip()
            for header, body in blocks.items()
            if "tls" in _directives(body)
            for host in header.split(",")
        }
        assert loaded_by == set(tls_preflight.proxied_hosts(deploy_central.DOMAIN))

    def test_letsencrypt_mode_loads_no_certificate_at_all(self) -> None:
        blocks = _caddyfile("LetsEncrypt")
        assert [h for h, b in blocks.items() if "tls" in _directives(b)] == []
