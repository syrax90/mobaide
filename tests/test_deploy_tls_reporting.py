"""Pins what the TLS work in ``deploy/prod`` actually SHOWS an operator, and
which exception the auto-rollback path reads.

The rules themselves live in ``tls_preflight`` and are covered by
``test_deploy_tls_preflight.py``. This file covers the two callers, where the
failure modes are different in kind and none of them are visible from a pure
function:

  - an affirmative "the origin cert is clean" printed about material nothing
    ever parsed (``deploy_central.remote_preflight``);
  - a TLS finding that scrolled off the top of a failed deploy
    (``deploy_central.run``'s tail re-print, which is the entire compensating
    control for "warn, never fail");
  - ``CertVerificationError`` widened past the two certificate verdicts --
    callers read that class as "do not auto-rollback", so a server that is
    simply down would keep a broken release live (``wait_http``);
  - "Transient -- re-run the deploy" printed for the post-swap probe, which
    demands a 200 from this host and therefore cannot conclude that from a
    clean ``diagnose`` (``deploy_web.report_unreachable``);
  - a second, possibly contradictory verdict for a failure ``wait_http``
    already classified and printed (``deploy_web.remote_preflight``).

No network, no ssh, no server, no wall clock: the SSH round-trip, the clock,
``urlopen`` and the classifier are monkeypatched, and every assertion reads the
captured stdout the operator would see.
"""
from __future__ import annotations

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
import deploy_web  # noqa: E402
import tls_preflight  # noqa: E402


# `openssl x509 -noout -ext subjectAltName` as the remote preflight sends it
# back, after its `tr '\n' ' '` squash.
REISSUED_SAN_LINE = (
    "ORIGIN_SANS=X509v3 Subject Alternative Name:      "
    "DNS:api.mobaide.com, DNS:mobaide.com, DNS:www.mobaide.com "
)
WILDCARD_SAN_LINE = (
    "ORIGIN_SANS=X509v3 Subject Alternative Name:      "
    "DNS:*.mobaide.com, DNS:mobaide.com "
)
SHADOW_OK = "Origin cert SANs do not shadow app.mobaide.com"
PAIR_OK = "Origin cert and key are a matching pair"


def _explodes(what: str) -> Callable[..., Any]:
    def fake(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{what} must not be reached here")

    return fake


def _explodes_with(exc: BaseException) -> Callable[..., Any]:
    def fake(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return fake


def _preflight_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *facts: str,
) -> tuple[list[tls_preflight.TlsWarning], str]:
    """Run ``remote_preflight`` against a canned remote stdout."""
    stdout = "\n".join(("USER=deploy", *facts, "OK"))
    monkeypatch.setattr(
        deploy_central,
        "invoke_ssh",
        lambda script: deploy_central.SshResult(0, stdout),
    )
    _, warnings = deploy_central.remote_preflight("CloudflareOrigin")
    return warnings, capsys.readouterr().out


class TestRemotePreflightOriginReassurances:
    """Each affirmative is asserted separately and only where evidence exists.

    The two findings are independent, so one firing must not mute the other's
    all-clear -- and neither all-clear may be printed for a fact the server
    never managed to measure.
    """

    def test_the_reissued_production_cert_earns_both_all_clears(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        warnings, out = _preflight_output(
            monkeypatch,
            capsys,
            "ORIGIN_PRESENT=1",
            REISSUED_SAN_LINE,
            "ORIGIN_CERT_PUB=8f4e1c",
            "ORIGIN_KEY_PUB=8f4e1c",
        )
        assert warnings == []
        assert f"[OK] {SHADOW_OK}" in out
        assert f"[OK] {PAIR_OK}" in out
        assert "[!!]" not in out
        assert "[XX]" not in out

    def test_a_wildcard_cert_keeps_the_pair_all_clear_it_earned(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        warnings, out = _preflight_output(
            monkeypatch,
            capsys,
            "ORIGIN_PRESENT=1",
            WILDCARD_SAN_LINE,
            "ORIGIN_CERT_PUB=8f4e1c",
            "ORIGIN_KEY_PUB=8f4e1c",
        )
        assert [w.kind for w in warnings] == ["shadow"]
        assert "[XX]" in out
        assert "'*.mobaide.com'" in out
        assert SHADOW_OK not in out
        assert f"[OK] {PAIR_OK}" in out

    def test_a_crossed_pair_keeps_the_shadow_all_clear_it_earned(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        warnings, out = _preflight_output(
            monkeypatch,
            capsys,
            "ORIGIN_PRESENT=1",
            REISSUED_SAN_LINE,
            "ORIGIN_CERT_PUB=8f4e1c",
            "ORIGIN_KEY_PUB=b02d99",
        )
        assert [w.kind for w in warnings] == ["pair"]
        assert "[XX] origin-cert.pem and origin-key.pem are not a pair" in out
        assert f"[OK] {SHADOW_OK}" in out
        assert PAIR_OK not in out

    def test_an_unreadable_cert_earns_no_all_clear_at_all(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The blocking fault this whole path was rebuilt for: openssl failed,
        so neither fact exists, and both checks have to say so. Claiming "no
        wildcard, keys match" here is a false all-clear about exactly the cert
        the guard exists to catch -- worse than no guard, because it reassures.
        """
        warnings, out = _preflight_output(monkeypatch, capsys, "ORIGIN_PRESENT=1")
        assert warnings == []
        assert SHADOW_OK not in out
        assert PAIR_OK not in out
        assert "wildcard-shadow check did NOT run" in out
        assert "cert/key pair check did NOT run" in out
        assert out.count("[!!]") == 2

    def test_absent_pems_are_reported_as_missing_not_as_unreadable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        warnings, out = _preflight_output(monkeypatch, capsys, "ORIGIN_CERT_MISSING=1")
        assert warnings == []
        assert "CloudflareOrigin mode needs them" in out
        assert out.count("[!!]") == 1
        assert SHADOW_OK not in out
        assert PAIR_OK not in out


class TestRunReprintsOutstandingWarnings:
    """The tail re-print is the whole compensating control for TLS findings warning instead
    of failing the deploy. ``print_summary`` runs only on the fully successful path, so
    every OTHER exit is exactly the run whose output an operator scrolls back through.
    """

    @staticmethod
    def _args() -> Any:
        return deploy_central.build_parser().parse_args([])

    def test_a_failed_deploy_still_ends_with_the_finding(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_deploy(args: Any, tls_warnings: list[Any]) -> int:
            tls_warnings.append(
                tls_preflight.TlsWarning("shadow", "wildcard headline", ("remedy",))
            )
            deploy_central.fail("docker compose failed (exit 1)")
            return 1

        monkeypatch.setattr(deploy_central, "_run_deploy", fake_deploy)
        assert deploy_central.run(self._args()) == 1
        out = capsys.readouterr().out
        assert "Outstanding TLS warnings" in out
        assert out.rindex("wildcard headline") > out.rindex("docker compose failed")

    def test_an_exception_does_not_swallow_the_finding(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_deploy(args: Any, tls_warnings: list[Any]) -> int:
            tls_warnings.append(
                tls_preflight.TlsWarning("pair", "pair headline", ("remedy",))
            )
            raise deploy_central.DeployError("scp tar failed")

        monkeypatch.setattr(deploy_central, "_run_deploy", fake_deploy)
        with pytest.raises(deploy_central.DeployError):
            deploy_central.run(self._args())
        assert "pair headline" in capsys.readouterr().out

    def test_a_clean_run_prints_no_warning_section(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            deploy_central, "_run_deploy", lambda args, tls_warnings: 0
        )
        assert deploy_central.run(self._args()) == 0
        assert "Outstanding TLS warnings" not in capsys.readouterr().out


class TestRunTlsDiagnosisGuards:
    """``--diagnose-tls`` short-circuits ``run`` before ``check_local_tools``,
    so this function is the first thing to touch ssh and has to guard it.
    """

    @pytest.fixture(autouse=True)
    def _no_remote_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            deploy_central, "remote_preflight", _explodes("the ssh preflight")
        )

    def test_letsencrypt_mode_says_the_origin_checks_were_skipped(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The origin-cert section is why most operators reach for this command;
        printing an empty heading reads as "checked, nothing found".
        """
        monkeypatch.setattr(deploy_central, "diagnose_local_tls", lambda urls: 0)
        assert deploy_central.run_tls_diagnosis("LetsEncrypt") == 0
        out = capsys.readouterr().out
        assert "Origin-cert checks skipped" in out
        assert "--tls-mode CloudflareOrigin" in out

    def test_missing_ssh_is_reported_instead_of_tracebacking(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(deploy_central, "diagnose_local_tls", lambda urls: 0)
        monkeypatch.setattr(
            deploy_central, "shutil", types.SimpleNamespace(which=lambda tool: None)
        )
        assert deploy_central.run_tls_diagnosis("CloudflareOrigin") == 0
        assert "ssh not found in PATH" in capsys.readouterr().out

    def test_a_local_failure_still_sets_the_exit_code_when_the_rest_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(deploy_central, "diagnose_local_tls", lambda urls: 2)
        assert deploy_central.run_tls_diagnosis("LetsEncrypt") == 1

    def test_a_failed_ssh_preflight_is_a_skip_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(deploy_central, "diagnose_local_tls", lambda urls: 0)
        monkeypatch.setattr(
            deploy_central,
            "shutil",
            types.SimpleNamespace(which=lambda tool: "/usr/bin/ssh"),
        )
        monkeypatch.setattr(
            deploy_central,
            "remote_preflight",
            _explodes_with(deploy_central.DeployError("")),
        )
        assert deploy_central.run_tls_diagnosis("CloudflareOrigin") == 1
        assert "the ssh preflight failed" in capsys.readouterr().out

    def test_an_origin_finding_makes_the_command_exit_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The local hosts verify fine, so only the server-side finding can move
        this exit code -- which is what makes the flag usable from a script.
        """
        monkeypatch.setattr(deploy_central, "diagnose_local_tls", lambda urls: 0)
        monkeypatch.setattr(
            deploy_central,
            "shutil",
            types.SimpleNamespace(which=lambda tool: "/usr/bin/ssh"),
        )
        monkeypatch.setattr(
            deploy_central,
            "remote_preflight",
            lambda mode: (
                "deploy",
                [tls_preflight.TlsWarning("shadow", "headline", ())],
            ),
        )
        assert deploy_central.run_tls_diagnosis("CloudflareOrigin") == 1


class TestDiagnoseLocalTls:
    def test_the_control_probe_runs_once_and_its_answer_is_handed_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Comparing a failing host against a known-good one only means anything
        if both used the SAME trust store, so the resolved context has to reach
        every call -- and the verdict, once settled, must not be re-derived per
        host (each run walks every address of every control URL).
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        probed: list[Any] = []
        calls: list[dict[str, Any]] = []

        def record_probe(ctx: Any) -> tls_preflight.ControlResult:
            probed.append(ctx)
            return tls_preflight.ControlResult("https://www.google.com/", True)

        def record_diagnose(url: str, **kwargs: Any) -> None:
            calls.append({"url": url, **kwargs})
            return None

        monkeypatch.setattr(deploy_central, "http_ssl_context", lambda: context)
        monkeypatch.setattr(tls_preflight, "control_probe", record_probe)
        monkeypatch.setattr(tls_preflight, "diagnose", record_diagnose)

        failures = deploy_central.diagnose_local_tls(
            (deploy_central.HEALTH_URL, deploy_central.APP_URL)
        )

        assert failures == 0
        assert probed == [context]
        assert [c["control_verified"] for c in calls] == [True, True]
        assert [c["context"] for c in calls] == [context, context]

    def test_a_dead_local_trust_store_does_not_skip_probing_the_hosts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A trust store that cannot verify ANY host is still not a reason to
        skip probing: what each host actually serves is evidence the operator
        needs, and only the per-host probe below can produce it.
        """
        probed_urls: list[str] = []

        def record_diagnose(url: str, **kwargs: Any) -> tls_preflight.ProbeFailure:
            probed_urls.append(url)
            return tls_preflight.ProbeFailure("tls_local", "why it failed", "what to do")

        monkeypatch.setattr(deploy_central, "http_ssl_context", lambda: None)
        monkeypatch.setattr(
            tls_preflight,
            "control_probe",
            lambda ctx: tls_preflight.ControlResult(None, True),
        )
        monkeypatch.setattr(tls_preflight, "diagnose", record_diagnose)

        failures = deploy_central.diagnose_local_tls(
            (deploy_central.HEALTH_URL, deploy_central.APP_URL)
        )

        assert failures == 3
        assert probed_urls == [deploy_central.HEALTH_URL, deploy_central.APP_URL]
        out = capsys.readouterr().out
        assert "Control probe FAILED" in out
        assert "pip install certifi" in out


class _FakeClock:
    """Deterministic stand-in for the ``time`` module: ``sleep`` advances the
    clock instead of blocking, so the poll loop runs a fixed number of passes.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class TestWaitHttpRollbackContract:
    """``CertVerificationError`` means "do not auto-rollback"."""

    @pytest.fixture(autouse=True)
    def _no_network_no_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deploy_central, "time", _FakeClock())
        monkeypatch.setattr(deploy_central, "http_ssl_context", lambda: None)
        monkeypatch.setattr(
            deploy_central.urllib.request,
            "urlopen",
            _explodes_with(
                urllib.error.URLError(
                    ssl.SSLCertVerificationError(
                        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
                    )
                )
            ),
        )

    @pytest.mark.parametrize(
        ("kind", "suppresses_rollback"),
        [
            ("tls_local", True),
            ("tls_server", True),
            ("timeout", False),
            ("connect", False),
        ],
    )
    def test_only_certificate_verdicts_suppress_the_auto_rollback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        suppresses_rollback: bool,
    ) -> None:
        """``cert_error`` is latched by ANY poll iteration, so the state at the
        deadline can be something else entirely -- Caddy serving an untrusted
        cert while provisioning, then the container dying. Raising the cert
        class for a server that is simply down suppresses the rollback and
        leaves the broken release live.
        """
        monkeypatch.setattr(
            tls_preflight,
            "diagnose",
            lambda *args, **kwargs: tls_preflight.ProbeFailure(
                kind, "why it failed", "what to do"
            ),
        )
        with pytest.raises(deploy_central.DeployError) as excinfo:
            deploy_central.wait_http(deploy_central.HEALTH_URL, 4)

        raised_cert_class = isinstance(
            excinfo.value, deploy_central.CertVerificationError
        )
        assert raised_cert_class is suppresses_rollback
        assert "why it failed" in str(excinfo.value)

    def test_an_unreproducible_cert_error_stays_conservative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The classifier found nothing wrong on its own pass, so the only
        evidence left is the latched verification failure. A rollback cannot fix
        a certificate fault, so this stays the no-rollback class.
        """
        monkeypatch.setattr(tls_preflight, "diagnose", lambda *args, **kwargs: None)
        with pytest.raises(deploy_central.CertVerificationError) as excinfo:
            deploy_central.wait_http(deploy_central.HEALTH_URL, 4)
        assert "could not verify the TLS certificate" in str(excinfo.value)


class TestReportUnreachable:
    """``expect_200`` mirrors the caller's ``wait_http`` contract.

    ``diagnose`` accepts ANY HTTP status as reachable and follows redirects, so
    for the post-swap probe -- which runs ``follow_redirects=False`` and needs a
    200 from this host -- a clean ``diagnose`` is not "nothing is wrong": it is
    positive evidence that transport is fine and the RELEASE is at fault.
    """

    @pytest.fixture(autouse=True)
    def _no_real_ssl_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # deploy_web binds this name via `from deploy_central import (...
        # http_ssl_context ...)`, so it must be patched HERE, not on deploy_central:
        # report_unreachable looks the name up in deploy_web's own module namespace, and
        # patching the source module would not be seen.
        monkeypatch.setattr(deploy_web, "http_ssl_context", lambda: None)

    def test_a_post_swap_200_miss_is_release_content_not_a_transient(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """"Re-run the deploy" would re-ship the same bad release, or paper over
        a redirect away from the app host.
        """
        monkeypatch.setattr(tls_preflight, "diagnose", lambda url, **kwargs: None)
        deploy_web.report_unreachable(deploy_web.APP_URL, expect_200=True)
        out = capsys.readouterr().out
        assert "Transient" not in out
        assert "release" in out
        assert "readlink /opt/mobaide/www/current" in out

    def test_the_preflight_probe_keeps_the_transient_wording(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """That probe polls with ``any_status=True``, so a direct probe
        succeeding right after it gave up really is a transient.
        """
        monkeypatch.setattr(tls_preflight, "diagnose", lambda url, **kwargs: None)
        deploy_web.report_unreachable(deploy_web.APP_URL, expect_200=False)
        out = capsys.readouterr().out
        assert "Transient" in out
        assert "re-run the deploy" in out
        assert "readlink" not in out

    @pytest.mark.parametrize("expect_200", [True, False])
    def test_a_classified_transport_fault_outranks_both(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        expect_200: bool,
    ) -> None:
        monkeypatch.setattr(
            tls_preflight,
            "diagnose",
            lambda url, **kwargs: tls_preflight.ProbeFailure(
                "dns", "app.mobaide.com does not resolve", "Create the DNS record."
            ),
        )
        deploy_web.report_unreachable(deploy_web.APP_URL, expect_200=expect_200)
        out = capsys.readouterr().out
        assert "dns: app.mobaide.com does not resolve" in out
        assert "Create the DNS record." in out
        assert "Transient" not in out
        assert "readlink" not in out


class TestRemotePreflightClassifiedFailure:
    """``wait_http`` already prints a classified verdict before raising; a
    caller that re-derives one through ``report_unreachable`` risks a second,
    possibly contradictory verdict for the exact same failure.
    """

    def test_a_classified_wait_http_failure_skips_report_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            deploy_web, "invoke_ssh", lambda script: deploy_central.SshResult(0, "OK")
        )
        monkeypatch.setattr(
            deploy_web,
            "wait_http",
            _explodes_with(deploy_web.ClassifiedDeployError("already printed")),
        )
        monkeypatch.setattr(
            deploy_web, "report_unreachable", _explodes("report_unreachable")
        )

        with pytest.raises(deploy_web.DeployError) as excinfo:
            deploy_web.remote_preflight()

        assert str(excinfo.value) == ""
