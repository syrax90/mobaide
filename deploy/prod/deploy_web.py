#!/usr/bin/env python3
"""deploy_web.py — Ship the browser SPA (web/) to app.mobaide.com.

Usage:
    python deploy/prod/deploy_web.py                # build + deploy
    python deploy/prod/deploy_web.py --skip-build   # reuse existing web/dist
    python deploy/prod/deploy_web.py --rollback     # repoint at previous release
    python deploy/prod/deploy_web.py --force        # dirty tree / hash mismatch OK
    python deploy/prod/deploy_web.py --diagnose-tls # classify a TLS failure, deploy nothing

What it does:
  1) Preflight: local tools, web/node_modules, clean tree, npm build, CSP-hash guard
  2) Remote preflight: /opt/mobaide/www exists, https://app.mobaide.com reachable
  3) Tar web/dist -> scp -> extract to www/releases/<tag>/
  4) Atomic symlink swap: ln -s <tag> current.new && mv -T current.new current
  5) Prune to the newest 5 releases; poll https://app.mobaide.com/ for 200

Separate from deploy_central.py on purpose: a web deploy swaps one symlink —
no container restart, no source-dir rollback, a different health target —
so the only shared parts are the SSH plumbing helpers imported below.
Because the swap is atomic there is NO auto-rollback: a failed health probe
after a swap almost always means DNS/Caddy, not release content, and the
--rollback flag is a one-liner if the release itself is bad.

Server-side artifacts:
    /opt/mobaide/www/releases/<tag>/   one dir per deployed release
    /opt/mobaide/www/current           relative symlink to releases/<tag>
                                       (relative so it resolves inside the
                                       caddy container's /srv/www bind mount)
    /opt/mobaide/www/.previous         release live before the last swap
                                       (--rollback target; each rollback
                                       toggles it; deleting it degrades
                                       rollback to the sorted predecessor)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Running `python deploy/prod/deploy_web.py` puts deploy/prod on sys.path[0],
# so both sibling modules import directly. deploy_central guards its entry
# point with __main__, and its module level holds only constants; tls_preflight
# is pure and imports neither of these back, so there is no cycle.
import tls_preflight
from deploy_central import (
    APP_URL,
    DEPLOY_DIR,
    PROJECT_ROOT,
    REMOTE_BASE,
    SSH_HOST,
    CertVerificationError,
    ClassifiedDeployError,
    DeployError,
    check_clean_tree,
    diagnose_local_tls,
    dim,
    enable_ansi,
    fail,
    http_ssl_context,
    info,
    invoke_ssh,
    invoke_ssh_or_fail,
    ok,
    report_probe_failure,
    require_min_python,
    resolve_tag,
    step,
    wait_http,
    warn,
)

WEB_DIR = PROJECT_ROOT / "web"
DIST_DIR = WEB_DIR / "dist"
CADDYFILES = (
    DEPLOY_DIR / "Caddyfile.LetsEncrypt",
    DEPLOY_DIR / "Caddyfile.CloudflareOrigin",
)
KEEP_RELEASES = 5

_INLINE_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def check_local_tools(skip_build: bool) -> None:
    tools = ["ssh", "scp", "tar", "git"]
    if not skip_build:
        tools.append("npm")
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        # shutil.which resolves npm.cmd on Windows, so a miss is a real miss.
        raise DeployError(f"not found in PATH: {', '.join(missing)}")
    ok(f"Local tools present: {', '.join(tools)}")


def build_web(skip_build: bool) -> None:
    if skip_build:
        if not (DIST_DIR / "index.html").exists():
            raise DeployError(
                f"--skip-build but {DIST_DIR}/index.html is missing; "
                f"run `npm run build` in web/ first"
            )
        warn(f"--skip-build: reusing existing {DIST_DIR}")
        return
    step("Building web/ (npm run build)")
    npm = shutil.which("npm")  # resolves npm.cmd on Windows
    if npm is None:
        raise DeployError("npm not found in PATH")
    if not (WEB_DIR / "node_modules").exists():
        # node_modules is not in the repo, so `npm run build` on a fresh clone
        # dies inside vite as `sh: tsc: command not found` / exit 127, which
        # names neither the missing directory nor the fix.
        raise DeployError(
            f"{WEB_DIR / 'node_modules'} is missing — `npm run build` would fail "
            f"with `tsc: command not found`. Install dependencies first:\n"
            f"    cd {WEB_DIR} && npm ci"
        )
    r = subprocess.run([npm, "run", "build"], cwd=WEB_DIR)
    if r.returncode != 0:
        raise DeployError(f"npm run build failed (exit {r.returncode})")
    if not (DIST_DIR / "index.html").exists():
        raise DeployError(f"build produced no {DIST_DIR}/index.html")
    ok("web/dist built")


def check_csp_hash(force: bool) -> None:
    """Verify the built inline theme-boot script matches the Caddyfile CSP.

    The app.mobaide.com site block pins the pre-paint theme-boot inline
    <script> by sha256 hash in `script-src`. If web/index.html changed and
    the Caddyfiles did not, the browser silently refuses to run the script
    (theme flash on load — cosmetic, but pointlessly shipped). Hard-fail
    here so the drift is fixed at the source instead of discovered in prod.
    """
    step("CSP-hash guard (inline theme-boot script vs Caddyfiles)")
    html = (DIST_DIR / "index.html").read_text(encoding="utf-8")
    scripts = _INLINE_SCRIPT_RE.findall(html)
    if not scripts:
        ok("No inline <script> in dist/index.html — nothing to pin")
        return
    problems = []
    for script in scripts:
        digest = hashlib.sha256(script.encode("utf-8")).digest()
        token = f"sha256-{base64.b64encode(digest).decode('ascii')}"
        for caddyfile in CADDYFILES:
            if token not in caddyfile.read_text(encoding="utf-8"):
                problems.append((token, caddyfile.name))
    if not problems:
        ok(f"Inline script hash pinned in both Caddyfiles ({len(scripts)} script)")
        return
    for token, name in problems:
        fail(f"'{token}' missing from deploy/prod/{name}")
    dim("  The inline theme-boot script in web/index.html changed. Update the")
    dim("  script-src hash in BOTH deploy/prod/Caddyfile.* files, deploy the")
    dim("  Caddyfile change via deploy_central.py, then re-run this script.")
    if force:
        warn("--force: continuing despite CSP-hash mismatch (theme boot will be blocked)")
        return
    raise DeployError("CSP hash mismatch (see above; --force to override)")


def report_unreachable(url: str, *, expect_200: bool) -> None:
    """Say which of {DNS, connect, local CA store, server cert} is at fault.

    `expect_200` mirrors the caller's `wait_http` contract. `diagnose` treats
    any HTTP status as reachable and follows redirects, so for the probes that
    demand a 200 from this host, "no transport failure" is not "nothing is
    wrong" — it is the signal that the fault is release content.
    """
    failure = tls_preflight.diagnose(url, context=http_ssl_context())
    if failure is not None:
        report_probe_failure(failure)
        return
    if expect_200:
        fail(f"{url} answered, but not with a 200 from this host.")
        dim("  Transport, DNS and the certificate are all fine — this is release")
        dim("  content: a missing index.html, or a redirect away from the app host.")
        dim(f"  Check: ssh {SSH_HOST} 'readlink {REMOTE_BASE}/www/current'")
        return
    fail(f"{url} unreachable while polling, but a direct probe just succeeded.")
    dim("  Transient — re-run the deploy.")


def remote_preflight() -> None:
    """One SSH round-trip verifying the server is ready for a web release."""
    probe = invoke_ssh(
        f"""set +e
errs=""
[ -d {REMOTE_BASE} ]      || errs="$errs BASE_MISSING"
[ -d {REMOTE_BASE}/www ]  || errs="$errs WWW_MISSING"
[ -w {REMOTE_BASE}/www ]  || errs="$errs WWW_NOT_WRITABLE"
if [ -n "$errs" ]; then echo "ERRORS:$errs"; exit 1; fi
echo OK
"""
    )
    if probe.exit_code != 0:
        errs = ""
        for line in probe.output.splitlines():
            if line.startswith("ERRORS:"):
                errs = line[len("ERRORS:"):].strip()
        if not errs:
            fail(f"Cannot ssh {SSH_HOST}. Raw error:")
            for line in probe.output.splitlines():
                dim(line)
        else:
            if "BASE_MISSING" in errs:
                fail(f"{REMOTE_BASE} missing (run deploy/prod/bootstrap-mobaide.sh)")
            if "WWW_MISSING" in errs:
                fail(
                    f"{REMOTE_BASE}/www missing — re-run bootstrap-mobaide.sh, or run "
                    f"deploy_central.py once (its extract step creates www/releases), "
                    f"or: ssh {SSH_HOST} 'mkdir -p {REMOTE_BASE}/www/releases'"
                )
            if "WWW_NOT_WRITABLE" in errs:
                fail(
                    f"{REMOTE_BASE}/www not writable. If dockerd created it "
                    f"root-owned (compose mounted it before it existed), run: "
                    f"ssh {SSH_HOST} 'sudo chown -R $(whoami): {REMOTE_BASE}/www'"
                )
        raise DeployError("")
    ok(f"Server ready ({REMOTE_BASE}/www writable)")

    step(f"Probing {APP_URL} (any HTTP status = Caddy site block live)")
    try:
        # 404 is fine (empty webroot pre-first-deploy); only transport-level
        # failure means the site block / DNS record does not exist yet.
        wait_http(APP_URL, 15, any_status=True)
    except ClassifiedDeployError:
        # wait_http already printed the classification; re-running it would
        # print a second, possibly contradictory verdict.
        raise DeployError("")
    except CertVerificationError as e:
        # An empty message means "details already printed" — but the
        # unclassified fallback prints nothing, so carry the reason.
        raise DeployError(str(e) or f"{APP_URL}: TLS verification failed")
    except DeployError:
        report_unreachable(APP_URL, expect_200=False)
        raise DeployError("")
    ok(f"{APP_URL} reachable")


def build_release_tag() -> str:
    # Lexicographic order == chronological order; the pruner and --rollback
    # both rely on `sort` over these names.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{resolve_tag()}"


def ship_release(tag: str) -> None:
    step(f"Packaging web/dist as release {tag}")
    tar_local = Path(tempfile.gettempdir()) / f"mobaide-web-{tag}.tar"
    tar_local.unlink(missing_ok=True)
    try:
        r = subprocess.run(
            ["tar", "-cf", str(tar_local), "dist"], cwd=WEB_DIR,
        )
        if r.returncode != 0:
            raise DeployError(f"tar failed (exit {r.returncode})")
        ok(f"Archive built: {tar_local} ({tar_local.stat().st_size:,} bytes)")

        step(f"Uploading to {SSH_HOST} and swapping atomically")
        r = subprocess.run(
            ["scp", str(tar_local), f"{SSH_HOST}:{REMOTE_BASE}/web-release.tar"]
        )
        if r.returncode != 0:
            raise DeployError("scp web release failed")

        invoke_ssh_or_fail(
            f"""set -e
cd {REMOTE_BASE}/www
mkdir -p releases
rm -rf "releases/{tag}"
mkdir "releases/{tag}"
tar -xf {REMOTE_BASE}/web-release.tar -C "releases/{tag}" --strip-components=1
rm -f {REMOTE_BASE}/web-release.tar
# Remember what `current` pointed at BEFORE this swap: rollback must return
# to the previously-live release, not guess from directory order (sorted
# order cannot distinguish "one release back" from "a newer build that was
# rolled away from").
prev_target=$(readlink current 2>/dev/null || true)
rm -f current.new
ln -s "releases/{tag}" current.new
# mv -T (GNU) renames onto the existing symlink atomically. Plain mv would
# move current.new INTO the target dir, and ln -sfn leaves an
# unlink+create window.
mv -T current.new current
if [ -n "$prev_target" ]; then printf '%s\\n' "$prev_target" > .previous; fi
# Keep the newest {KEEP_RELEASES} (tags sort chronologically; `current`
# always points at the newest by construction, so it is never pruned; the
# bootstrap placeholder sorts oldest and ages out here). The grep keeps the
# recorded rollback target alive even when repeated deploy/rollback cycles
# push it out of the newest-{KEEP_RELEASES} window — pruning it would
# silently degrade --rollback to the sorted-predecessor fallback, i.e. a
# newer, just-rejected build. (grep mid-pipeline: its exit code is masked
# by xargs under set -e without pipefail, and an empty $prev_target
# matches nothing, so every candidate still prunes.)
ls -1d releases/* | sort | head -n -{KEEP_RELEASES} | grep -v -x -F "$prev_target" | xargs -r rm -rf
echo "current -> $(readlink current)"
""",
            "Remote extract/swap failed",
        )
        ok("Release live (previous releases retained for rollback)")
    finally:
        tar_local.unlink(missing_ok=True)


def do_rollback() -> int:
    step("Rollback: repoint current at the previously-live release")
    out = invoke_ssh_or_fail(
        f"""set -e
cd {REMOTE_BASE}/www
now=$(readlink current || true)
# Prefer the recorded pre-swap target (written by every deploy): directory
# order alone would pick the NEWEST non-current release, which rolls
# forward if newer builds exist on disk. Fall back to the sorted
# predecessor of `current` for hosts that predate the .previous marker,
# and drop the marker if its target was pruned meanwhile.
prev=""
if [ -f .previous ]; then
    prev=$(cat .previous)
    [ -n "$prev" ] && [ -d "$prev" ] || prev=""
fi
if [ -z "$prev" ]; then
    prev=$(ls -1d releases/* | sort | awk -v cur="$now" '$0 == cur {{exit}} {{last = $0}} END {{print last}}')
fi
if [ -z "$prev" ] || [ "$prev" = "$now" ]; then echo NO_PREV; exit 1; fi
rm -f current.new
ln -s "$prev" current.new
mv -T current.new current
# Make rollback a toggle: a second --rollback returns to where we were.
if [ -n "$now" ]; then printf '%s\\n' "$now" > .previous; fi
echo "current -> $prev"
""",
        "No previous web release to roll back to"
        f" (need >=2 dirs in {REMOTE_BASE}/www/releases)",
    )
    for line in out.splitlines():
        info(line)
    # follow_redirects=False: a misconfigured DNS record redirecting to the
    # (healthy) apex must not count as the app host serving the release.
    wait_http(APP_URL, 60, follow_redirects=False)
    ok(f"Rollback live at {APP_URL}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.diagnose_tls:
        return 1 if diagnose_local_tls((APP_URL,)) else 0
    if args.rollback:
        return do_rollback()

    step("Preflight")
    check_local_tools(args.skip_build)
    if args.force:
        warn("--force: skipping clean-tree check")
    else:
        check_clean_tree()

    build_web(args.skip_build)
    check_csp_hash(args.force)
    remote_preflight()

    tag = build_release_tag()
    ship_release(tag)

    step(f"Waiting for {APP_URL} to serve the release")
    try:
        # follow_redirects=False for the same reason as the rollback probe:
        # only a 200 from the app host itself proves the release is live.
        wait_http(APP_URL, 60, follow_redirects=False)
    except CertVerificationError as e:
        # wait_http already classified and printed this one.
        fail(f"Post-deploy probe inconclusive: {e}")
        warn("The swap is already live. A certificate fault is never fixed by a rollback.")
        info("Rollback (only if the release itself is bad): python deploy/prod/deploy_web.py --rollback")
        return 1
    except ClassifiedDeployError as e:
        # Already printed by wait_http — report the outcome, not the diagnosis.
        fail(f"Post-deploy probe failed: {e}")
        warn("The swap is already live; a probe failure here is usually DNS/Caddy, not release content.")
        info("Rollback: python deploy/prod/deploy_web.py --rollback")
        return 1
    except DeployError as e:
        # The swap already happened (atomically), so the operator needs the
        # rollback hint HERE, not only on the success path.
        fail(f"Post-deploy probe failed: {e}")
        report_unreachable(APP_URL, expect_200=True)
        warn("The swap is already live; a probe failure here is usually DNS/Caddy, not release content.")
        info("Rollback: python deploy/prod/deploy_web.py --rollback")
        return 1
    ok(f"Healthy: {APP_URL}")

    info("")
    info(f"Deployed web release {tag}")
    info(f"Rollback: python deploy/prod/deploy_web.py --rollback")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deploy the browser SPA to app.mobaide.com")
    p.add_argument(
        "--skip-build", action="store_true",
        help="reuse the existing web/dist instead of running npm run build",
    )
    p.add_argument(
        "--rollback", action="store_true",
        help="repoint the current symlink at the previous release",
    )
    p.add_argument(
        "--force", action="store_true",
        help="allow dirty working tree and CSP-hash mismatch",
    )
    p.add_argument(
        "--diagnose-tls", action="store_true",
        help=f"classify why {APP_URL} does not verify, then exit without deploying",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("deploy_web.py"):
        return 1
    args = build_parser().parse_args(argv)
    enable_ansi()
    try:
        return run(args)
    except DeployError as e:
        if str(e):
            fail(str(e))
        return 1
    except KeyboardInterrupt:
        fail("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
