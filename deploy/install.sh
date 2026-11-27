#!/usr/bin/env bash
#
# Mobaide agent install script (Linux + macOS).
#
# Usage (curl|bash):
#     curl -fsSL https://get.mobaide.com/install.sh | bash
#
# Idempotent: re-running upgrades the agent in place. Each release is
# staged into a sibling ``releases/<version>/`` directory and the
# ``current_version`` pointer is rewritten atomically; the launcher boots
# into whichever release is current.
#
# Wrap everything in main() and call it once at the bottom so a partial
# download (curl|sh) cannot execute half a script.

set -euo pipefail

# Python must not import modules from the directory this script is run in:
# a file named like a standard module there would run inside the checks below.
export PYTHONSAFEPATH=1

# Defaults (override via env var).
: "${MOBAIDE_AGENT_PREFIX:=}"
: "${MOBAIDE_AGENT_MANIFEST_URL:=https://api.mobaide.com/api/v1/agent/manifest.json}"
# Public half of the key that signs every release manifest (base64 Ed25519).
# The script carries it, so no server is asked which key to trust. Set
# MOBAIDE_AGENT_TRUST_KEY_B64 to install from your own release pipeline.
MOBAIDE_RELEASE_PUBLIC_KEY_B64="f+cIAgdrhuXQtkHjRI5NSYjKAAc0aUWWZP7s7eIjunw="
# The oldest agent release this script installs: the first one whose signed
# data pins every dependency by hash. A server that offers an older signed
# release is refused. release_agent.py refuses a release below this number.
MOBAIDE_MINIMUM_AGENT_VERSION="0.3.38"
: "${MOBAIDE_AGENT_TRUST_KEY_B64:=}"
# Not used by this script; the agent commands it runs inherit it. "1" lets
# the agent reach a relay over cleartext http/ws: only on a trusted local network.
: "${MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH:=0}"
# The agent runs as a real user (not a system account) so it can read/write
# their own /home/<user>/<repo> source trees. With `sudo curl|bash` this
# resolves from $SUDO_USER; override explicitly with MOBAIDE_RUN_AS_USER for
# CI / multi-user hosts where SUDO_USER is unset or wrong.
: "${MOBAIDE_RUN_AS_USER:=}"
: "${MOBAIDE_AGENT_INSTALL_URL:=https://get.mobaide.com/install.sh}"

# The `mobaide-agent` command the banners advertise. The marker line lets a
# re-run, the root uninstall helper and mobaide.agent.self_uninstall tell our
# shim apart from a file someone else put there; keep the three in sync.
CLI_SHIM_PATH="/usr/local/bin/mobaide-agent"
CLI_SHIM_MARKER="mobaide-agent CLI shim"

info()  { printf '[INFO]  %s\n' "$*"; }
warn()  { printf '[WARN]  %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
fail()  { error "$*"; exit 1; }

detect_platform() {
    case "$(uname -s)" in
        Linux)  PLATFORM="linux" ;;
        Darwin) PLATFORM="darwin" ;;
        *)      fail "Unsupported OS: $(uname -s). Linux and macOS only." ;;
    esac

    if [ -z "$MOBAIDE_AGENT_PREFIX" ]; then
        if [ "$PLATFORM" = "linux" ]; then
            MOBAIDE_AGENT_PREFIX="/opt/mobaide"
        else
            MOBAIDE_AGENT_PREFIX="/usr/local/mobaide"
        fi
    fi
}

require_root_when_writing_system_dir() {
    case "$MOBAIDE_AGENT_PREFIX" in
        /opt/*|/usr/*|/etc/*) ;;
        *) return 0 ;;
    esac
    [ "$(id -u)" -eq 0 ] && return 0

    # `sudo curl ... | bash` only elevates curl, not bash, so this branch is
    # the common case rather than an edge case. Re-fetch the script under
    # sudo so the canonical one-liner Just Works.
    if command -v sudo >/dev/null 2>&1; then
        info "Installing to $MOBAIDE_AGENT_PREFIX requires root — re-executing under sudo."
        info "(With 'curl|bash', sudo applies only to curl; auto-elevating now.)"
        exec sudo \
            --preserve-env=MOBAIDE_AGENT_PREFIX,MOBAIDE_AGENT_MANIFEST_URL,MOBAIDE_AGENT_TRUST_KEY_B64,MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH,MOBAIDE_RUN_AS_USER,MOBAIDE_AGENT_INSTALL_URL \
            bash -c 'curl -fsSL "$1" | bash' _ "$MOBAIDE_AGENT_INSTALL_URL"
    fi

    fail "Installing to $MOBAIDE_AGENT_PREFIX requires root, and sudo is not available.
Re-run as root, or set MOBAIDE_AGENT_PREFIX to a user-writable directory:
    MOBAIDE_AGENT_PREFIX=\$HOME/.mobaide curl -fsSL $MOBAIDE_AGENT_INSTALL_URL | bash"
}

check_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

check_macos_version() {
    # The agent installs binary packages only, and its dependency lock has
    # them from macOS 11 on. Keep the number in sync with LOCK_MACOS_MINIMUM
    # in deploy/prod/release_agent.py.
    [ "$PLATFORM" = "darwin" ] || return 0
    local version major
    version="$(sw_vers -productVersion 2>/dev/null || true)"
    major="${version%%.*}"
    case "$major" in
        ''|*[!0-9]*)
            warn "Could not read the macOS version; continuing."
            return 0
            ;;
    esac
    if [ "$major" -lt 11 ]; then
        fail "macOS $version is too old: the Mobaide agent needs macOS 11 or newer."
    fi
}

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 is required (>= 3.12)."
    fi
    # Intentionally NOT `local`: $version is consumed downstream by
    # ensure_ensurepip_available to pick the right `python3.X-venv` apt
    # package on Debian/Ubuntu. Same script-scope idiom this file already
    # uses for $tmpdir in stage_release().
    version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    local major minor
    major="${version%%.*}"
    minor="${version##*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        fail "Python 3.12+ is required (found $version)."
    fi
    info "Python $version detected."
    # The agent installs binary packages only, and its dependency lock has them
    # for 64-bit Python on x86-64 and ARM64. Keep in sync with
    # LOCK_TARGET_PLATFORMS in deploy/prod/release_agent.py. A probe that does
    # not answer lets the install go on.
    local build
    build="$(python3 -c 'import platform, struct; print(platform.machine(), struct.calcsize("P") * 8)' 2>/dev/null || true)"
    case "$build" in
        ''|'x86_64 64'|'amd64 64'|'aarch64 64'|'arm64 64') ;;
        *)
            fail "Python $version on this computer is a ${build##* }-bit build for ${build% *}. The Mobaide agent needs 64-bit Python on an x86-64 or ARM64 computer: it installs prebuilt packages, and they exist only for those."
            ;;
    esac
    # Python 3.15 and later on glibc Linux: one dependency has binary packages
    # only for glibc 2.39 or newer. Keep both numbers in sync with
    # LOCK_PLATFORM_FOR_PYTHON in deploy/prod/release_agent.py.
    if [ "$PLATFORM" = "linux" ] && [ "$minor" -ge 15 ]; then
        local glibc
        glibc="$(python3 -c 'import platform; lib, ver = platform.libc_ver(); print(ver if lib == "glibc" else "")' 2>/dev/null || true)"
        if [ -n "$glibc" ] && ! python3 -c 'import sys; sys.exit(0 if tuple(int(p) for p in sys.argv[1].split(".")[:2]) >= (2, 39) else 1)' "$glibc" 2>/dev/null; then
            fail "Python $version on this system (glibc $glibc) cannot install the agent: the agent needs glibc 2.39 or newer with Python 3.15 and later.
Put Python 3.12, 3.13 or 3.14 first on PATH as 'python3' and run the installer again."
        fi
    fi
}

ensure_ensurepip_available() {
    if [ "$PLATFORM" != "linux" ]; then return 0; fi
    if python3 -c 'import ensurepip' >/dev/null 2>&1; then return 0; fi

    info "python3 'ensurepip' module missing — required by 'python3 -m venv'."
    info "Common on Debian/Ubuntu when the python3-venv package is not installed."

    if [ "$(id -u)" -ne 0 ]; then
        fail "Cannot auto-install the venv prerequisite without root.
You are installing into MOBAIDE_AGENT_PREFIX=$MOBAIDE_AGENT_PREFIX (user-writable),
so the script did not auto-elevate. Install the package manually and re-run:
    Debian/Ubuntu: sudo apt-get install -y python${version}-venv  (or python3-venv)
    Fedora/RHEL:   sudo dnf install -y python3-virtualenv
    Arch:          sudo pacman -S --noconfirm python
    openSUSE:      sudo zypper install -y python3-virtualenv
    Alpine:        sudo apk add py3-virtualenv"
    fi

    # Captured package-manager output, surfaced only if every attempt fails.
    # Each command keeps stdin on /dev/null (no interactive prompt under
    # ``curl|bash``) and folds stderr into the capture; nothing prints on the
    # happy path. ``pm_out``/``apt_out`` must stay BARE assignments inside the
    # ``if``/``&&`` below — a ``local pm_out="$(...)"`` would return ``local``'s
    # own exit status and mask the command's, silently inverting the fallback
    # (ShellCheck SC2155).
    local installed=0 pm_out="" apt_out=""
    if command -v apt-get >/dev/null 2>&1; then
        info "Installing python${version}-venv via apt-get (non-interactive)."
        export DEBIAN_FRONTEND=noninteractive
        # Refresh lists FIRST, best-effort (exit status deliberately ignored):
        # a fresh/minimal image (Docker, debootstrap, a cleaned cloud image)
        # ships an empty /var/lib/apt/lists, so a cold install fails with
        # "Unable to locate package"; and a single dead mirror or an expired
        # Release signature on an UNRELATED repo makes ``apt-get update`` exit
        # non-zero even when the target installs cleanly — so its status must
        # NOT gate the install. The previous ``apt-get update && apt-get
        # install`` chain skipped the install on any such hiccup, the opaque
        # failure this fixes. Its output is kept so a dead mirror surfaces in
        # the diagnostic below (the real cause often shows here, not in install).
        pm_out="$(apt-get update -qq </dev/null 2>&1 || true)"
        if apt_out="$(apt-get install -y --no-install-recommends "python${version}-venv" </dev/null 2>&1)" \
           || apt_out="$(apt-get install -y --no-install-recommends python3-venv </dev/null 2>&1)"; then
            installed=1
        fi
        pm_out="${pm_out}
${apt_out}"
    elif command -v dnf >/dev/null 2>&1; then
        info "Installing python3-virtualenv via dnf."
        pm_out="$(dnf install -y python3-virtualenv </dev/null 2>&1)" && installed=1
    elif command -v yum >/dev/null 2>&1; then
        info "Installing python3-virtualenv via yum."
        pm_out="$(yum install -y python3-virtualenv </dev/null 2>&1)" && installed=1
    elif command -v pacman >/dev/null 2>&1; then
        info "Refreshing pacman db and ensuring python (includes venv)."
        pm_out="$(pacman -Sy --noconfirm --needed python </dev/null 2>&1)" && installed=1
    elif command -v zypper >/dev/null 2>&1; then
        info "Installing python3-virtualenv via zypper."
        pm_out="$(zypper --non-interactive install python3-virtualenv </dev/null 2>&1)" && installed=1
    elif command -v apk >/dev/null 2>&1; then
        info "Installing py3-virtualenv via apk."
        pm_out="$(apk add --no-cache py3-virtualenv </dev/null 2>&1)" && installed=1
    else
        fail "No supported package manager found (tried apt-get, dnf, yum, pacman, zypper, apk).
Install the Python venv/ensurepip prerequisite for your distro and re-run."
    fi

    if [ "$installed" -ne 1 ]; then
        if [ -n "$pm_out" ]; then
            warn "Package manager output (the underlying failure follows):"
            printf '%s\n' "$pm_out" >&2
        fi
        fail "Package manager invocation failed for the venv prerequisite.
Try manually: install python${version}-venv (Debian/Ubuntu) or your distro's equivalent."
    fi

    if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
        fail "Installed the venv package but 'import ensurepip' still fails.
Open an issue with the output of: python3 -c 'import ensurepip'; uname -a; cat /etc/os-release"
    fi
    info "ensurepip available."
}

# The library that checks the manifest signature, pinned: exact versions and
# the SHA-256 of every file, binary packages only. cryptography 46.0.3 is the
# newest version with binary packages for Intel macOS and for Windows on ARM64;
# its public advisories are in parts that one Ed25519 check does not use. A
# cryptography that python3 already has is used as it is (the fast path in
# resolve_verifier_python): the pin covers what this script downloads.
# install.ps1 carries the same list. release_agent.py checks that both match
# and install on every supported platform; --update-verifier-pin rewrites them.
write_verifier_requirements() {
    cat > "$1" <<'VERIFIER_REQUIREMENTS'
cffi==2.1.1 ; platform_python_implementation != 'PyPy' \
    --hash=sha256:046bfc24911b37851ee1b51aab8bffe713d89c68c6a057b09484ce9fd5f69b4e \
    --hash=sha256:06c72bb76605a4b0cd0aad6930b69d4baf7dd5d806cfc409b824191099700e66 \
    --hash=sha256:0beceaabe56af686895136a2de78db54ecd8e4046b236b8fd6d6cb61389e9bf2 \
    --hash=sha256:154852545011f779917b11c78db2358d095da62a9a172b78ad0a583ee5adc0d0 \
    --hash=sha256:194cffa889098ced9976c3fc6340305e43f6303657d298da55366907c05c22d6 \
    --hash=sha256:19ee6127ee34de7d83ce3d371ebc5ed91addbdcc39f9ab15ce4eb35a4e534971 \
    --hash=sha256:1a18a57b58cfb21fc28d72e876acf10eaed67a1ed96226f92af4df681d571c4c \
    --hash=sha256:1aa5645c30469b09530c4ebca77ebf8f17618293c58f8549cb1a543a50236e7d \
    --hash=sha256:1dea0e4d7d4f11f619fe8c1d76caf49e24405b4b5743c0e3be16a500ecd930c9 \
    --hash=sha256:208f941bb9d18e768138677f0a6d2ce01f590df56043dda1df1535ac57c88517 \
    --hash=sha256:210019b6c7cf07f081b4c54635c8cf744377001350e29cc0f81c4377b4797735 \
    --hash=sha256:246fa40ce8645a614ff682e0b70f37134e460eaf93a775e0cbe3cca585a67a80 \
    --hash=sha256:25792eac27877609e7bb06d42ff88278a6624fff2ba9bbb523c09616b117e80f \
    --hash=sha256:27350daa11d4f10c540e6e89dada4c54feb7256ad03e9a4dc075ebad7ba360d1 \
    --hash=sha256:28907ab9bfb6aa13184cfc17c6b8e1023c5ab6fd7076d8c20a35e59fe04f8f29 \
    --hash=sha256:2ae64be792b8966f2c69538199728b290e34726562896df1e5dc8ffd8d8188e8 \
    --hash=sha256:31348097ff5bbe827ccc41795d4dd099d9f0625e7def00ee653c137a490c2a6c \
    --hash=sha256:3143d81e29e1e20a9ce10901ec369012947876596f75a222235965f2b7ae832e \
    --hash=sha256:3222ba5d678f80a030e6afbcc33dc1ae5cb45facabb61cee2c7016b8432fde48 \
    --hash=sha256:3311ed60d36f83378794e1009ac6258bafbf81f7888b4caa7b35a521e3f95813 \
    --hash=sha256:334644fbac4eff73d985a17a91226df55d0f394160c4cfb880e084c8f7161cac \
    --hash=sha256:34e261f78cb6ceaaa36f42f2613f4380d94d9c759a9c73c769ee6e0247364632 \
    --hash=sha256:363e05fa78e15116c3c32c210ee36884fd6b9afa6d440e47112c3bd511d64cb6 \
    --hash=sha256:398aff33cee2767e3e781d2554c54bd0dff386bb437581e0d8011fde1a942ec1 \
    --hash=sha256:3d22a20b1fb1632cc72c22f95f7b0d2961c3e1c235f245ba4c606c4771035659 \
    --hash=sha256:42a494cee34437f05546455144f2b5d9ac09b1face62bcfce597d2e521066688 \
    --hash=sha256:42e2f76b9455f5a9a844f770bf3e200ed3da0e15f5df3db9c31fe80b04b3d004 \
    --hash=sha256:42f6930c31dc7f50732c9ae793c2786c7b6b044195967bbdde40bb9be81c4cc0 \
    --hash=sha256:456a61fa52d579ebf9df2e9552ead5129855dbaff6c1e5a9b1bc408809bdc062 \
    --hash=sha256:471cee653ae88de62096552e6d24ccb4a5adb8c8c9f10b5054d0122c15bf2779 \
    --hash=sha256:49cbc70e6542d4ccccb936558d1064a8012541e78f821f955cff24e357776c94 \
    --hash=sha256:4a7c934f7360e8cd64fe9efadcbd10c7c6364f531e432b9a4bf5ccbc9e0e8b50 \
    --hash=sha256:4be96343e422f2dfcd12ab5c9f5aebe03f82f737c6bffeca6830b3875cb44aab \
    --hash=sha256:4f42141fc14250de6dde5ee7ea4432be017252d91f19c5ad043c084cea629cac \
    --hash=sha256:507a24c282e0f42f8ed737cf048572cbf580468da5555764a8331735e9c736b6 \
    --hash=sha256:51b31d1c98274844cfd7838ce00bfc27c7423a4dc00fc0772fc3331c2cc90676 \
    --hash=sha256:58acb8ab8e295e6c5ea12f888cbb13cf21511ef2a3303a23f4325c29d17fe5c1 \
    --hash=sha256:5a59cc1c4442bc3d5c703bf720b51138d0bfc173618807c9ee2490a7541dd3d9 \
    --hash=sha256:5bb4e7ea95dcd6a014a6fef62e62467d67d8e582326443f3d68e71d6320a9fcf \
    --hash=sha256:5c58fe613dc5e5336357eff555824a314d8e43282600435c8d1cb6a7a2fedd13 \
    --hash=sha256:5e7cecbaadb83884793e05828cee59b210b24583b9c7425d0ba6a754fe22eb4e \
    --hash=sha256:616f097f2fe415bc92a247f02e11f634e1f9e9a83d327e3c915c15089c87869e \
    --hash=sha256:63bbfd5ded17c4840ac07cd8f1c21ba9d9708141f840b324f422f41b207e3973 \
    --hash=sha256:64faea20f4e2613363a1a9b9c7dd73058f3ecd00133a511e72ad7c511658f527 \
    --hash=sha256:661c298b4821edebead0c91edd2b00374d67ad7c5a1f7a91d4442633b79d6a72 \
    --hash=sha256:68e62fe11f30d5ca8289242866f0a5291402d8529ca2178ab8afc5c9694ae890 \
    --hash=sha256:6a8dddef476fab96d066d578fc88526767b836ab5ab21754e1d5bf3879c31c7c \
    --hash=sha256:6e192623c49c94421616a5778fba35cf0d5a8d000650c1967ef4448ee5cdd990 \
    --hash=sha256:7225e4514edb64eb6740324353e0da0711954fd8d7da4576755b1c6e09b697cd \
    --hash=sha256:75f80557d1389eddbd0de2681f6a390a0c5338c31ddaa821381c203fc3fd50d9 \
    --hash=sha256:770de9db11e84213beec501cfcaa013b019820ca881e03344dea5844f7876d94 \
    --hash=sha256:7750c6449dff7864bb9bb27ddfb0267756189201a3afc911d82b3caacd70dfc3 \
    --hash=sha256:7bde5e4cc5c10140859842b9d383af292b22639a4dffb725314baf45968cef80 \
    --hash=sha256:7ce713ace7c0e4520535b42b77eaa742c16dab813978064913e5a3cf82973b41 \
    --hash=sha256:7da0c5eff80f0197f3b3d1232ec5a682a9325f4ae9016a78f5f5ca35f9ced1f5 \
    --hash=sha256:7dbb61fe3a7699468030f71bbe5f8a0e326a151daa91beb11a6fc1f980c55e1c \
    --hash=sha256:811bd1e21d32de12efca32393a0ab3f5133b54fce9bd44b8bd77ab07da14bf6a \
    --hash=sha256:8ef53b2de9bcb9197d31854256575d59dbac0cba72ac627bb291ef5eceb74be4 \
    --hash=sha256:937c0052c05a31ca1daf18de3158eed4dbfcb9cc107adbea227728d647be701e \
    --hash=sha256:9d2055050ea716bd38b7f7f1579c275386646b4894c155a3e2f3cd62ed41b7c6 \
    --hash=sha256:9f8d177621de5cb38ee3e731eda45d421db093ec0739f46a5594babda7987a98 \
    --hash=sha256:a2d7755bef5a12ed488f4ef1f1b69ee9191d7396083b755a5d2295f6edb4768b \
    --hash=sha256:a48d62ab9d6f4f98c983223a547af44be6ca3691074c31cecced6facd3ba2dc1 \
    --hash=sha256:a4f00aa42f75d6e4595e8866e748cc1705adc0cddfeb2ca86d0d03993d63ba03 \
    --hash=sha256:a6e721d4b0e45d5b65e87534470e67b18dcd092c83f68fba09f152b9cbc061af \
    --hash=sha256:a730a083190634c65cca36ba5f489531576ebd79bcd5c8e172130f6453127231 \
    --hash=sha256:a931079504ecc49efed7744c476a5c343a92fabf66dec2db95edb1b2fdc770e2 \
    --hash=sha256:aa9511c62d14da7aacc9b4bf51f3f697a621e83b2d6919008243c3aad168eea3 \
    --hash=sha256:ab36d55f9ed2d067327667c2fea18dda018eb628dd6347aa01dda6cf1f5d3836 \
    --hash=sha256:ad2c86c495b899d862ea0f4b42891b8713a3bd45dd4105c7fd51c2a72f39f3a5 \
    --hash=sha256:aeae0e330c9f6acd681f647d46cefd30c29f93e3392882e792e82080c9691399 \
    --hash=sha256:b0431303acaea1089ad4b3e9ce4e6518193def1118d4073ca848635ee4ea2e96 \
    --hash=sha256:b5bdfd1c873d4e093aabc0ca84c4ca6dbc4f752afb5c86f146d9742580c9da2e \
    --hash=sha256:baed1e86cc735622097354b9d1281406caf42ff42a886d29faa8e8d1630333be \
    --hash=sha256:c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf \
    --hash=sha256:c26608d2222fb1e94487e4a387d85f13eb55d5ed725cb25a0c589ac4ee60e7bc \
    --hash=sha256:c7659f22557c5a0bc4855cd635f55edec690cc008a40768527762cb9fb263455 \
    --hash=sha256:c8c69575568085ba0b1b10c0249d779a214aea6f6522e949a0fc9fb0fcb449d0 \
    --hash=sha256:c8d2c9fd1f2d16f780d15127abb050d13d1a76c03a4bd87d7e4980e45e511e12 \
    --hash=sha256:ca82be1a1d406ecfe1d25dc16cb33488e5a16bf4438c9fb590484ea29d92478b \
    --hash=sha256:cc572dace3f60ef98d7b12ff411d20f5362feb31a0439eab0085bbfd349982d7 \
    --hash=sha256:d18e5ac0f2f03f4f518d3e23db0f0cad7faa1da8620e9c09461d443bbf6e6692 \
    --hash=sha256:d28630f5854ab07ab1fd4aba756de52326c82e6be15d414b12793f1975048b54 \
    --hash=sha256:d9c275eaacd24aa73f94ffd6de08fc3f932424d8b6c376f4bed7cde376fe7bc3 \
    --hash=sha256:da0e573f9f97159390c89d9f1a9e41908b66d408cc5b58d08cf3847d844c531b \
    --hash=sha256:dd31f52ea1086513bb9df30f8fcee9b8918323ae067a3d5b78bc826a000712be \
    --hash=sha256:dddad92b554513a31f272570678ba307fb9f618f05e3d4a5eacafff9eae03e1d \
    --hash=sha256:df423d40ee8654634421812bc3b196da3f9bd7d32929da813f8394c4348a5358 \
    --hash=sha256:df913725b79db7bcf03448f36b7bf8815363417d5b58deecf9305e3e30f0f21a \
    --hash=sha256:e0bcb7e0f677f543555d2adff3bf19c05f66cdb4796e5ff602442ab2fe3c4ef7 \
    --hash=sha256:e2d65b31f36619cda3999b78b2aa9632e76b78448e7a56fc4240824200e7c4fc \
    --hash=sha256:e6e8cff14d6fb0be70a09c0bdc58096f501952d04624ebf867e0e56da2df8960 \
    --hash=sha256:f16c709686a78c727bbbf059f92b0bf41c6fc60deec706d2dc19f529175a6125 \
    --hash=sha256:f24fb43132a4c6b4cb4eb029492919b2db645be6808d738f244fd146c03c32cb \
    --hash=sha256:f53e442b08449d42821fa4a4fba000095af9f62742a500f978a9f557ec44339a \
    --hash=sha256:f5cfbc5fe74540d335175b656c725d74d90e3730c626d92575eea35029d9afaa \
    --hash=sha256:f81b3b8f3d4e343550fa4baa0e479bba9f2d29ce9c2e9b51d1ce1718d7442fcf \
    --hash=sha256:f8ec5e643a9a937f64e1999eb9f75d072263751912dc5cd06d3c85f8f44be7c3 \
    --hash=sha256:fb92203a88b3d3053034db775110081c49d28be6551923805e039924093761e4 \
    --hash=sha256:fcd22650c908d7b7da162bbfaab594a1227a15d1643a98c68b122ac642fa2264
cryptography==46.0.3 \
    --hash=sha256:00a5e7e87938e5ff9ff5447ab086a5706a957137e6e433841e9d24f38a065217 \
    --hash=sha256:01ca9ff2885f3acc98c29f1860552e37f6d7c7d013d7334ff2a9de43a449315d \
    --hash=sha256:09859af8466b69bc3c27bdf4f5d84a665e0f7ab5088412e9e2ec49758eca5cbc \
    --hash=sha256:0abf1ffd6e57c67e92af68330d05760b7b7efb243aab8377e583284dbab72c71 \
    --hash=sha256:1000713389b75c449a6e979ffc7dcc8ac90b437048766cef052d4d30b8220971 \
    --hash=sha256:109d4ddfadf17e8e7779c39f9b18111a09efb969a301a31e987416a0191ed93a \
    --hash=sha256:10b01676fc208c3e6feeb25a8b83d81767e8059e1fe86e1dc62d10a3018fa926 \
    --hash=sha256:10ca84c4668d066a9878890047f03546f3ae0a6b8b39b697457b7757aaf18dbc \
    --hash=sha256:15ab9b093e8f09daab0f2159bb7e47532596075139dd74365da52ecc9cb46c5d \
    --hash=sha256:191bb60a7be5e6f54e30ba16fdfae78ad3a342a0599eb4193ba88e3f3d6e185b \
    --hash=sha256:22d7e97932f511d6b0b04f2bfd818d73dcd5928db509460aaf48384778eb6d20 \
    --hash=sha256:23b1a8f26e43f47ceb6d6a43115f33a5a37d57df4ea0ca295b780ae8546e8044 \
    --hash=sha256:36e627112085bb3b81b19fed209c05ce2a52ee8b15d161b7c643a7d5a88491f3 \
    --hash=sha256:39b6755623145ad5eff1dab323f4eae2a32a77a7abef2c5089a04a3d04366715 \
    --hash=sha256:3b51b8ca4f1c6453d8829e1eb7299499ca7f313900dd4d89a24b8b87c0a780d4 \
    --hash=sha256:402b58fc32614f00980b66d6e56a5b4118e6cb362ae8f3fda141ba4689bd4506 \
    --hash=sha256:416260257577718c05135c55958b674000baef9a1c7d9e8f306ec60d71db850f \
    --hash=sha256:46acf53b40ea38f9c6c229599a4a13f0d46a6c3fa9ef19fc1a124d62e338dfa0 \
    --hash=sha256:4b7387121ac7d15e550f5cb4a43aef2559ed759c35df7336c402bb8275ac9683 \
    --hash=sha256:50fc3343ac490c6b08c0cf0d704e881d0d660be923fd3076db3e932007e726e3 \
    --hash=sha256:516ea134e703e9fe26bcd1277a4b59ad30586ea90c365a87781d7887a646fe21 \
    --hash=sha256:549e234ff32571b1f4076ac269fcce7a808d3bf98b76c8dd560e42dbc66d7d91 \
    --hash=sha256:5d7f93296ee28f68447397bf5198428c9aeeab45705a55d53a6343455dcb2c3c \
    --hash=sha256:5ecfccd2329e37e9b7112a888e76d9feca2347f12f37918facbb893d7bb88ee8 \
    --hash=sha256:6276eb85ef938dc035d59b87c8a7dc559a232f954962520137529d77b18ff1df \
    --hash=sha256:6b5063083824e5509fdba180721d55909ffacccc8adbec85268b48439423d78c \
    --hash=sha256:6eae65d4c3d33da080cff9c4ab1f711b15c1d9760809dad6ea763f3812d254cb \
    --hash=sha256:6f61efb26e76c45c4a227835ddeae96d83624fb0d29eb5df5b96e14ed1a0afb7 \
    --hash=sha256:71e842ec9bc7abf543b47cf86b9a743baa95f4677d22baa4c7d5c69e49e9bc04 \
    --hash=sha256:760f83faa07f8b64e9c33fc963d790a2edb24efb479e3520c14a45741cd9b2db \
    --hash=sha256:78a97cf6a8839a48c49271cdcbd5cf37ca2c1d6b7fdd86cc864f302b5e9bf459 \
    --hash=sha256:7ce938a99998ed3c8aa7e7272dca1a610401ede816d36d0693907d863b10d9ea \
    --hash=sha256:8a6e050cb6164d3f830453754094c086ff2d0b2f3a897a1d9820f6139a1f0914 \
    --hash=sha256:9394673a9f4de09e28b5356e7fff97d778f8abad85c9d5ac4a4b7e25a0de7717 \
    --hash=sha256:94cd0549accc38d1494e1f8de71eca837d0509d0d44bf11d158524b0e12cebf9 \
    --hash=sha256:a04bee9ab6a4da801eb9b51f1b708a1b5b5c9eb48c03f74198464c66f0d344ac \
    --hash=sha256:a23582810fedb8c0bc47524558fb6c56aac3fc252cb306072fd2815da2a47c32 \
    --hash=sha256:a2c0cd47381a3229c403062f764160d57d4d175e022c1df84e168c6251a22eec \
    --hash=sha256:a8b17438104fed022ce745b362294d9ce35b4c2e45c1d958ad4a4b019285f4a1 \
    --hash=sha256:a9a3008438615669153eb86b26b61e09993921ebdd75385ddd748702c5adfddb \
    --hash=sha256:b02cf04496f6576afffef5ddd04a0cb7d49cf6be16a9059d793a30b035f6b6ac \
    --hash=sha256:b419ae593c86b87014b9be7396b385491ad7f320bde96826d0dd174459e54665 \
    --hash=sha256:c0a7bb1a68a5d3471880e264621346c48665b3bf1c3759d682fc0864c540bd9e \
    --hash=sha256:c70cc23f12726be8f8bc72e41d5065d77e4515efae3690326764ea1b07845cfb \
    --hash=sha256:c8daeb2d2174beb4575b77482320303f3d39b8e81153da4f0fb08eb5fe86a6c5 \
    --hash=sha256:cb3d760a6117f621261d662bccc8ef5bc32ca673e037c83fbe565324f5c46936 \
    --hash=sha256:d55f3dffadd674514ad19451161118fd010988540cee43d8bc20675e775925de \
    --hash=sha256:d89c3468de4cdc4f08a57e214384d0471911a3830fcdaf7a8cc587e42a866372 \
    --hash=sha256:db391fa7c66df6762ee3f00c95a89e6d428f4d60e7abc8328f4fe155b5ac6e54 \
    --hash=sha256:dfb781ff7eaa91a6f7fd41776ec37c5853c795d3b358d4896fdbb5df168af422 \
    --hash=sha256:e5bf0ed4490068a2e72ac03d786693adeb909981cc596425d09032d372bcc849 \
    --hash=sha256:e7aec276d68421f9574040c26e2a7c3771060bc0cff408bae1dcb19d3ab1e63c \
    --hash=sha256:ef639cb3372f69ec44915fafcd6698b6cc78fbe0c2ea41be867f6ed612811963 \
    --hash=sha256:f260d0d41e9b4da1ed1e0f1ce571f97fe370b152ab18778e9e8f67d6af432018
pycparser==3.12 ; implementation_name != 'PyPy' and platform_python_implementation != 'PyPy' \
    --hash=sha256:a076f3f489b39b43ed13563a1dafe42447445bec2e294154ec5c57fcdda4c85e \
    --hash=sha256:f03387877eab82ce01bbdfd0ba7252ee55a90b1c708d5129e2ce915475cf1db6
VERIFIER_REQUIREMENTS
}

# Trust-key validation (``_validate_trust_key_or_fail``) and manifest-signature
# verification (``verify_manifest_signature``) import ``cryptography`` — a
# PyPI dep that is NOT in stdlib. On Debian/Ubuntu the system tools that
# depend on it (cloud-init, snapd) drag it in, but on macOS, Alpine, fresh
# Fedora minimal, and the python.org installer it is absent. Those checks
# run BEFORE the wheel is fetched (which carries cryptography transitively),
# so the install would hard-fail with ``ModuleNotFoundError`` on every host
# without a pre-existing cryptography install.
#
# Sets two script-scoped variables (intentionally NOT ``local`` — the
# centralized ``cleanup_on_exit`` trap installed by ``main()`` resolves
# ``$BOOTSTRAP_DIR`` from this scope, and ``local`` would hide it):
#   * ``VERIFIER_PYTHON`` — interpreter that can ``import cryptography``.
#     Either ``python3`` (fast path: host already has it) or the bootstrap
#     venv's python (slow path: ephemeral install).
#   * ``BOOTSTRAP_DIR`` — bootstrap venv root if created, empty string
#     otherwise. The cleanup trap deletes it on EXIT (success or failure).
resolve_verifier_python() {
    if python3 -c 'import cryptography' >/dev/null 2>&1; then
        info "cryptography already importable on python3; skipping bootstrap venv."
        VERIFIER_PYTHON="python3"
        BOOTSTRAP_DIR=""
        return 0
    fi
    BOOTSTRAP_DIR="$(mktemp -d)"
    info "Bootstrapping verification venv (hash-pinned cryptography)..."
    if ! python3 -m venv "$BOOTSTRAP_DIR"; then
        fail "Could not create a verification venv (python3 -m venv failed).
The base interpreter is missing or has a broken venv/ensurepip. Ensure
python${version}-venv (or the equivalent for your distro) is installed and
re-run."
    fi
    # ``--no-cache-dir`` matches stage_release's pip flags (line ~389) — this
    # is an ephemeral venv, the cache would be deleted with it.
    # ``--disable-pip-version-check`` + ``--quiet`` suppress noise on a
    # throwaway one-shot install. Errors still surface (pip writes them to
    # stderr and exits non-zero).
    #
    # Lock down pip's runtime configuration so a hostile-configured host
    # cannot redirect ``cryptography`` to an attacker-controlled mirror.
    # A compromised cryptography wheel could forge a passing manifest
    # signature verify — the very check this venv exists for — so the
    # bootstrap install must be its own root of trust on top of TLS.
    #
    # Two layers, both required:
    #   * ``--index-url https://pypi.org/simple`` — declares the canonical
    #     primary index explicitly.
    #   * ``--isolated`` — tells pip to ignore environment variables
    #     (``PIP_EXTRA_INDEX_URL``, ``PIP_FIND_LINKS``, ``PIP_TRUSTED_HOST``,
    #     ``PIP_INDEX_URL``, ...) AND user/system config files
    #     (``~/.pip/pip.conf``, ``/etc/pip.conf``). Without ``--isolated``,
    #     ``--index-url`` alone leaves ``PIP_EXTRA_INDEX_URL`` and
    #     ``PIP_FIND_LINKS`` in play — pip treats those as ADDITIVE sources
    #     with no priority, so an attacker publishes
    #     ``cryptography==999.0.0`` on their mirror and pip prefers it by
    #     version (classic dependency-confusion vector,
    #     CVE-2018-20225-class).
    #
    # The agent's release wheel goes through the manifest's ``wheel_url``
    # rather than PyPI, so this lockdown matches the trust posture of the
    # rest of the install. Operators behind an air-gapped/self-hosted PyPI
    # mirror can pre-install ``cryptography>=43.0`` on the system Python
    # (fast path), bypassing this slow-path entirely.
    #
    # --require-hashes accepts only the files write_verifier_requirements
    # lists; --only-binary refuses a source build, whose build tools no hash
    # covers.
    write_verifier_requirements "$BOOTSTRAP_DIR/verifier-requirements.txt"
    if ! "$BOOTSTRAP_DIR/bin/pip" install --isolated --no-cache-dir --disable-pip-version-check --quiet --index-url https://pypi.org/simple --require-hashes --only-binary :all: -r "$BOOTSTRAP_DIR/verifier-requirements.txt"; then
        fail "Could not install the cryptography package required to verify
the manifest signature. Most likely causes:
  * No network access to PyPI (pypi.org).
  * A corporate proxy blocking pypi.org — set HTTPS_PROXY / HTTP_PROXY.
  * No prebuilt wheel for this Python/arch (check
    https://pypi.org/project/cryptography for the matching wheel).
Workaround: pre-install on the system Python (pip install 'cryptography>=43.0')
and re-run."
    fi
    VERIFIER_PYTHON="$BOOTSTRAP_DIR/bin/python"
}

# Centralized EXIT trap installed by main() — single source of truth for the
# transient temp dirs this installer creates AND for restoring a release that
# stage_release moved aside but did not finish rebuilding:
#   * $tmpdir          — stage_release's tarball/wheel scratch (mktemp -d
#                        at line ~320). NOT local in stage_release so this
#                        trap can see it from a deferred-expansion context.
#   * $BOOTSTRAP_DIR   — resolve_verifier_python's bootstrap venv root.
#   * $release_backup  — when stage_release reinstalls over an existing
#                        same-version release it moves the live dir aside to
#                        $release_backup first; if `set -e` aborts before the
#                        rebuild completes, restore it so a single-release
#                        install is not left pointing at a deleted/half-built
#                        dir. stage_release clears $release_backup on success,
#                        making the restore a no-op on the happy path.
# ``${var:-}`` defaults are essential: ``set -u`` would otherwise trip when
# the trap fires before either variable is set (fail-fast paths in
# detect_platform / check_python). Future contributors adding a third
# temp dir must extend this function, not install a second trap that
# would override this one.
cleanup_on_exit() {
    [ -n "${tmpdir:-}" ] && rm -rf -- "$tmpdir"
    [ -n "${BOOTSTRAP_DIR:-}" ] && rm -rf -- "$BOOTSTRAP_DIR"
    if [ -n "${release_backup:-}" ] && [ -n "${release_dir:-}" ] \
       && [ -d "$release_backup" ]; then
        rm -rf -- "$release_dir"
        mv "$release_backup" "$release_dir"
    fi
    return 0
}

fetch_manifest() {
    local manifest_path="$1"
    info "Fetching manifest from $MOBAIDE_AGENT_MANIFEST_URL"
    if ! curl -fsSL --max-time 30 "$MOBAIDE_AGENT_MANIFEST_URL" -o "$manifest_path"; then
        fail "Could not fetch manifest from $MOBAIDE_AGENT_MANIFEST_URL"
    fi
}

# Centralized install-time asset download. Network I/O goes through curl (the
# OS-native trust store), never Python urllib: a python.org framework build
# whose bundled ``Install Certificates.command`` never ran has an EMPTY OpenSSL
# CA store, so urllib fails TLS verification with CERTIFICATE_VERIFY_FAILED even
# when curl to the same endpoint succeeds (fetch_manifest above already proves
# curl works against the same infra). Mirrors
# deploy/install.ps1's Invoke-AgentDownload, which downloads via Invoke-WebRequest
# for the identical root cause. The file:// / absolute-path branch preserves the
# air-gapped self-host path (offline mirrors, test fixtures) the old Python
# fetch() supported. curl is a required dependency (check_command curl in main).
#
# Resilience flags on the network fetch: ``--retry``/``--retry-connrefused``
# survive a transient blip (DNS hiccup, reset, momentary 5xx) instead of
# aborting the whole ``curl|bash`` install; ``--connect-timeout`` fail-fasts on
# an unreachable host while ``--max-time`` still caps a legitimately slow large
# transfer; ``--`` ends option parsing so a URL can never be read as a flag.
download_asset() {
    local url="$1" dest="$2" user_agent="$3"
    case "$url" in
        file://*) cp -f -- "${url#file://}" "$dest" ;;
        /*)       cp -f -- "$url" "$dest" ;;
        *)        curl -fsSL --connect-timeout 30 --max-time 300 --retry 3 --retry-connrefused \
                      -A "$user_agent" -o "$dest" -- "$url" \
                      || fail "Failed to download $url" ;;
    esac
}

# Round-trip validate that ``$MOBAIDE_AGENT_TRUST_KEY_B64`` decodes to a
# 32-byte Ed25519 public key, so a malformed key surfaces immediately at
# install time as "your key is malformed" rather than later as a confusing
# "manifest signature does not verify".
_validate_trust_key_or_fail() {
    "$VERIFIER_PYTHON" - "$MOBAIDE_AGENT_TRUST_KEY_B64" <<'PY'
import base64, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

key_b64 = sys.argv[1]
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
except Exception as exc:
    sys.exit(f"trust key is not a valid base64 Ed25519 public key: {exc}")
PY
}

# Trust key: the release public key this script carries, unless the operator
# pinned another one with ``MOBAIDE_AGENT_TRUST_KEY_B64``. The key never
# comes from the server that also serves the manifest: that server could
# then sign its own manifest.
bootstrap_trust_key() {
    if [ -n "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        info "Using operator-pinned trust key (MOBAIDE_AGENT_TRUST_KEY_B64)."
    else
        MOBAIDE_AGENT_TRUST_KEY_B64="$MOBAIDE_RELEASE_PUBLIC_KEY_B64"
    fi
    _validate_trust_key_or_fail
}

verify_manifest_signature() {
    local manifest_path="$1"
    if [ -z "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        # ``bootstrap_trust_key`` should have populated this — defensive check
        # only fires if a future refactor reorders the call sites.
        fail "Internal error: trust key not set before signature verification."
    fi
    info "Verifying manifest signature"
    "$VERIFIER_PYTHON" - "$manifest_path" "$MOBAIDE_AGENT_TRUST_KEY_B64" "$MOBAIDE_MINIMUM_AGENT_VERSION" <<'PY'
import base64, json, sys
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

manifest_path, public_key_b64, minimum = sys.argv[1:4]
record = json.loads(Path(manifest_path).read_text())
body, sig = record["body"], record["signature_b64"]
canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
        base64.b64decode(sig), canon
    )
except InvalidSignature:
    sys.exit(
        "manifest signature does not verify. A manifest from your own release "
        "pipeline needs its public key in MOBAIDE_AGENT_TRUST_KEY_B64."
    )


def version_key(value):
    parts = str(value).split(".")
    return tuple(int(p) for p in parts) if all(p.isascii() and p.isdigit() for p in parts) else None


offered = body.get("version")
offered_key = version_key(offered)
if offered_key is None or offered_key < version_key(minimum):
    sys.exit(
        f"the server offers agent version {offered}, but this installer installs "
        f"only version {minimum} or newer. Try again later. If it keeps "
        "failing, download the installer again."
    )
PY
}

resolve_base_python() {
    # When ``python3`` is itself a venv, ``sys._base_executable`` is the real
    # (non-venv) interpreter it was created from. Basing the release venv off
    # that stable interpreter — and clearing any active VIRTUAL_ENV/PYTHONHOME
    # for the ``-m venv`` call — keeps the release venv's interpreter from
    # symlinking into a transient/relocatable venv that, once moved or removed,
    # would leave a 'bad interpreter' shebang on pip / mobaide-agent. A no-op in
    # the normal case (system python3: ``_base_executable == executable``).
    local base
    base="$(python3 -c 'import sys; print(sys._base_executable or sys.executable)' 2>/dev/null || true)"
    if [ -n "$base" ] && [ -x "$base" ]; then
        printf '%s' "$base"
    else
        command -v python3
    fi
}

stage_release() {
    local manifest_path="$1"
    local target_version
    target_version="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['body']['version'])" "$manifest_path")"
    if [ -z "$target_version" ]; then
        fail "manifest is missing 'version'"
    fi
    # Defense-in-depth: the version is signature-verified, but reject any value
    # that is not a clean version token before it becomes a releases/<v> path
    # component — so a crafted/buggy-signer version like ../../etc/... cannot
    # escape the releases dir. Mirrors the wheel_basename path-separator guard.
    case "$target_version" in
        .|..|*[!0-9A-Za-z.+_-]*)
            fail "manifest 'version' has unsafe path characters: $target_version" ;;
    esac

    # NOT `local`: the centralized EXIT trap (cleanup_on_exit) restores these
    # from a deferred-expansion context if `set -e` aborts mid-stage — the same
    # reason `tmpdir` below is script-scoped. Every other function declares its
    # own `local release_dir`, so this script-scoped value never leaks to them.
    release_dir="${MOBAIDE_AGENT_PREFIX}/releases/${target_version}"
    info "Staging release $target_version into $release_dir"

    # Reinstall safely over an existing same-version release: move the live dir
    # ASIDE rather than deleting it. main() only restarts the service after
    # stage_release returns, so a download / checksum / extraction / venv / pip
    # failure must leave the prior release intact — deleting it up front would
    # leave a single-release install pointing at a missing/half-built dir with no
    # complete sibling for the self-heal wrapper to recover to. Building into a
    # FRESH empty dir also guarantees no stale completeness signal survives into
    # the rebuild window: both the prior '.installed' AND the prior
    # 'venv/bin/mobaide-agent' console script that `python -m venv` would leave
    # behind are gone, so a crash before the new sentinel correctly reads as
    # incomplete. The backup uses the updater's '.old-<v>-<pid>' scratch
    # convention, so it is skipped by every release selector (dotfile, non-version
    # name — see the launcher self-heal filter) and reclaimed by the agent's
    # _sweep_scratch_dirs if ever leaked. cleanup_on_exit restores it on a
    # mid-stage abort; the tail of this function drops it on success. Mirrors
    # install.ps1's Install-Release and the updater's _swap_into_place. Bounded to
    # releases/ by the charset guard above.
    release_backup=""
    if [ -e "$release_dir" ]; then
        release_backup="${MOBAIDE_AGENT_PREFIX}/releases/.old-${target_version}-$$"
        rm -rf "$release_backup"
        mv "$release_dir" "$release_backup"
    fi
    mkdir -p "$release_dir"
    # Intentionally NOT `local`: the centralized `cleanup_on_exit` trap
    # installed at the top of `main()` resolves `$tmpdir` from this scope
    # via deferred expansion (`[ -n "${tmpdir:-}" ] && rm -rf -- "$tmpdir"`).
    # With `local`, the variable would go out of scope before the trap
    # fires and `set -u` would trip on the guard. Keeping `tmpdir`
    # script-scoped also lets a half-finished stage_release (any path
    # that exits non-zero under `set -e` between mktemp and the explicit
    # cleanup below) still surrender the directory on EXIT.
    tmpdir="$(mktemp -d)"

    # Download the release assets with curl, then hand the local paths to a
    # network-free Python staging step. curl (OS-native trust store) replaces
    # the old urllib fetch because a python.org framework build whose bundled
    # ``Install Certificates.command`` never ran has an EMPTY OpenSSL CA store,
    # so urllib raises CERTIFICATE_VERIFY_FAILED even though curl to the same
    # host succeeds — see download_asset. Mirrors deploy/install.ps1.
    #
    # The two asset URLs come out of the signature-verified manifest via plain
    # ``python3 -c`` (local JSON, no network). Deliberately NOT a heredoc inside
    # ``$(...)``: macOS bash 3.2.57 re-scans such a body and aborts the install
    # at parse time — see test_install_sh_heredoc_no_subst.py.
    local tarball_url wheel_url
    tarball_url="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['body']['tarball_url'])" "$manifest_path")"
    wheel_url="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['body']['wheel_url'])" "$manifest_path")"
    [ -n "$tarball_url" ] || fail "manifest is missing 'tarball_url'"
    [ -n "$wheel_url" ] || fail "manifest is missing 'wheel_url'"

    # Identify as the installer so a Cloudflare-fronted wheel host doesn't
    # classify the request as the default curl UA (Bot Fight Mode 1010) —
    # mirrors deploy/install.ps1's installer User-Agent. The wheel lands under a
    # temp name; the staging step below renames it to its canonical PEP 427
    # filename (pip 25.2+ validates wheel filenames at install time).
    local user_agent="mobaide-agent-installer/${target_version}"
    download_asset "$tarball_url" "$tmpdir/agent.tar.gz" "$user_agent"
    download_asset "$wheel_url" "$tmpdir/wheel.download" "$user_agent"

    # Pure-local staging: verify sha256, rename the wheel to its canonical PEP
    # 427 name, extract the tarball, check the dependency lock from the
    # tarball against the hash the signed manifest names, and write the
    # hash-pinned requirements file for pip. Runs as a plain command (NOT
    # captured in ``$(...)``) so macOS bash 3.2 never re-scans the Python
    # body — see test_install_sh_heredoc_no_subst.py.
    python3 - "$manifest_path" "$tmpdir" "$release_dir" <<'PY'
import hashlib, json, posixpath, sys, tarfile, urllib.parse
from pathlib import Path

manifest_path, tmpdir, release_dir = sys.argv[1:4]
record = json.loads(Path(manifest_path).read_text())
body = record["body"]


def verify_sha256(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        sys.exit(f"sha256 mismatch for {path}")


def wheel_basename(url):
    if url.startswith("file://"):
        name = Path(url.removeprefix("file://")).name
    else:
        name = posixpath.basename(urllib.parse.urlparse(url).path)
    if any(ch in name for ch in ("/", "\\", "\x00")):
        sys.exit(
            f"manifest wheel_url basename contains path-separator chars: {name!r} (from {url!r})"
        )
    if not name.endswith(".whl") or name.count("-") < 4:
        sys.exit(
            f"manifest wheel_url has non-PEP427 filename: {name!r} (from {url!r})"
        )
    return name

tarball = Path(tmpdir) / "agent.tar.gz"
wheel = Path(tmpdir) / wheel_basename(body["wheel_url"])
# curl staged the wheel under a temp name; move it to its canonical PEP 427
# filename so pip 25.2+'s wheel-filename validator accepts it at install time.
(Path(tmpdir) / "wheel.download").replace(wheel)

verify_sha256(tarball, body["tarball_sha256"])
verify_sha256(wheel, body["wheel_sha256"])

with tarfile.open(tarball, mode="r:gz") as tar:
    tar.extractall(release_dir, filter="data")

lock_sha = str(body.get("dependency_lock_sha256") or "")
if not lock_sha:
    sys.exit(
        "the signed release lists no dependency hashes; this installer installs "
        "only releases that pin every dependency"
    )
lock = Path(release_dir) / "requirements.lock"
if not lock.is_file():
    sys.exit("release tarball has no requirements.lock")
verify_sha256(lock, lock_sha)
(Path(tmpdir) / "requirements.txt").write_text(
    lock.read_text(encoding="utf-8")
    + f"\nmobaide-agent @ {wheel.resolve().as_uri()}"
    f" --hash=sha256:{body['wheel_sha256'].lower()}\n",
    encoding="utf-8",
)
PY

    info "Creating venv in $release_dir/venv"
    local base_python
    base_python="$(resolve_base_python)"
    env -u VIRTUAL_ENV -u PYTHONHOME "$base_python" -m venv "$release_dir/venv"
    # Every dependency at the exact version and SHA-256 that the signed
    # manifest pins. --require-hashes refuses a file with another hash and
    # a dependency the lock does not list; --only-binary refuses source
    # builds, whose build tools no hash covers. pip is the bundled copy.
    info "Installing the agent with hash-pinned dependencies"
    "$release_dir/venv/bin/python" -m pip install --no-cache-dir \
        --disable-pip-version-check --no-input --require-hashes \
        --only-binary :all: -r "$tmpdir/requirements.txt" \
        || fail "Dependency install failed: a package did not match the versions and hashes that the signed release lists."

    rm -rf "$tmpdir"

    # Mark the release complete only after pip + the wheel are installed, so the
    # launcher self-heal / rollback / prune never select a half-built release.
    # Keep '.installed' in sync with install_paths.is_release_complete.
    printf '%s\n' "$target_version" > "$release_dir/.installed"

    printf '%s\n' "$target_version" > "${MOBAIDE_AGENT_PREFIX}/current_version.tmp"
    mv "${MOBAIDE_AGENT_PREFIX}/current_version.tmp" "${MOBAIDE_AGENT_PREFIX}/current_version"
    info "Pinned current_version=$target_version"

    # Rebuild succeeded and current_version now points at it — discard the saved
    # previous release and disarm the EXIT-trap restore (cleanup_on_exit keys off
    # a non-empty $release_backup).
    if [ -n "$release_backup" ]; then
        rm -rf "$release_backup"
        release_backup=""
    fi
}

write_trust_key() {
    # Always called after ``bootstrap_trust_key`` has populated
    # ``$MOBAIDE_AGENT_TRUST_KEY_B64``, so the file is always written.
    # The agent's auto-updater requires this file at runtime
    # (mobaide.agent.install_paths.read_trust_public_key).
    if [ -z "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        fail "Internal error: write_trust_key called without a bootstrapped key."
    fi
    mkdir -p "${MOBAIDE_AGENT_PREFIX}/trust"
    printf '%s\n' "$MOBAIDE_AGENT_TRUST_KEY_B64" > "${MOBAIDE_AGENT_PREFIX}/trust/central_server.pub"
}

write_agent_env() {
    # Generate <prefix>/mobaide_agent/.env on first install (idempotent).
    # No --force on installer-driven invocations: a paired agent's
    # RELAY_AGENT_ID/TOKEN are never clobbered by re-running the installer.
    local version
    version="$(cat "${MOBAIDE_AGENT_PREFIX}/current_version")"
    local release_dir="${MOBAIDE_AGENT_PREFIX}/releases/${version}"
    info "Writing prepared agent .env via init-env"
    "$release_dir/venv/bin/python" -m mobaide.agent.config init-env \
        --prefix "$MOBAIDE_AGENT_PREFIX"
}

# Parse a KEY=VALUE line from ``<prefix>/mobaide_agent/.env``.
# Mirrors the PowerShell helper in ``deploy/install.ps1``; used by
# ``wait_agent_healthy`` to discover the agent's HTTP port without
# importing Python. Naive split on the first ``=`` matches what
# pydantic-settings + the env-writer agree on at boot (one key per
# line, no quoting; see mobaide.agent.config.init-env).
read_env_value() {
    local path="$1" key="$2" default="${3:-}"
    if [ ! -f "$path" ]; then
        printf '%s' "$default"
        return 0
    fi
    local line value
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "$key"=*|" $key"=*|"	$key"=*)
                value="${line#*=}"
                printf '%s' "$value"
                return 0
                ;;
        esac
    done < "$path"
    printf '%s' "$default"
}

# Poll the agent's /api/health endpoint until it responds 200, or
# hard-fail with a journalctl / log-tail pointer. Same role as
# ``Wait-AgentHealthy`` in install.ps1: distinguishes "supervisor
# reported started, service silently crashed" from "service is
# healthy". On Linux, systemd starts quickly enough that this almost
# always succeeds on the first poll; the guard mostly catches
# crashes-at-boot that would previously have hidden behind the
# "rendered QR doesn't pair" symptom.
wait_agent_healthy() {
    local timeout_sec="${1:-60}"
    local env_file="${MOBAIDE_AGENT_PREFIX}/mobaide_agent/.env"
    local port
    port="$(read_env_value "$env_file" 'PORT' '8000')"
    local url="http://127.0.0.1:${port}/api/health"
    # main() captures this function's stdout into $health_json, so any
    # diagnostic must go to stderr or it would corrupt the JSON body
    # downstream. ``info`` writes to stdout by default; force stderr.
    info "Waiting for agent /api/health at ${url} (timeout ${timeout_sec}s)" >&2
    local deadline=$(( $(date +%s) + timeout_sec ))
    local body
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if body="$(curl -fsS --max-time 2 "$url" 2>/dev/null)"; then
            printf '%s' "$body"
            return 0
        fi
        sleep 0.5
    done
    if [ "$PLATFORM" = "linux" ]; then
        fail "Agent service registered but ${url} did not respond within ${timeout_sec}s.
Service may have crashed at startup. Inspect:
    systemctl status mobaide-agent
    journalctl -u mobaide-agent -n 200"
    else
        fail "Agent service registered but ${url} did not respond within ${timeout_sec}s.
Service may have crashed at startup. Inspect:
    launchctl print gui/\$(id -u)/com.mobaide.agent
    tail -n 200 ${MOBAIDE_AGENT_PREFIX}/agent.err.log"
    fi
}

show_pairing_qr_from_service() {
    # Render the pairing QR after the agent service is up. The running
    # service's boot-time pairing-monitor calls /api/pairing/initiate
    # and writes <prefix>/run/pending_pairing.json; this function waits
    # for the file and renders it via the CLI. Mirrors
    # ``Show-PairingQrFromService`` in install.ps1.
    local health_json="$1"
    local pending_timeout_sec="${2:-60}"
    if [ -n "${MOBAIDE_AGENT_SKIP_INSTALL_QR:-}" ]; then
        info "MOBAIDE_AGENT_SKIP_INSTALL_QR set; not rendering pairing QR."
        return 0
    fi
    # Re-install on a paired agent: pairing_pending=false → skip QR.
    # Use Python for the JSON read to stay portable across distros
    # (jq is not guaranteed; bash's no-JSON is a fact of life).
    local pairing_pending
    pairing_pending="$(printf '%s' "$health_json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("true")
else:
    print("true" if data.get("pairing_pending") else "false")
')"
    if [ "$pairing_pending" = "false" ]; then
        info "Agent reports pairing_pending=false; already paired. Skipping QR."
        return 0
    fi
    local pending_file
    pending_file="${MOBAIDE_AGENT_PREFIX}/run/pending_pairing.json"
    info "Waiting for pairing nonce at ${pending_file} (timeout ${pending_timeout_sec}s)"
    local deadline=$(( $(date +%s) + pending_timeout_sec ))
    while [ "$(date +%s)" -lt "$deadline" ] && [ ! -f "$pending_file" ]; do
        sleep 0.5
    done
    if [ ! -f "$pending_file" ]; then
        local log_check="grep pairing_monitor.no_pairing_key ${MOBAIDE_AGENT_PREFIX}/agent.out.log"
        if [ "$PLATFORM" = "linux" ]; then
            log_check="journalctl -u mobaide-agent -n 200 | grep pairing_monitor.no_pairing_key"
        fi
        fail "Agent service is up but did not initiate pairing within ${pending_timeout_sec}s
(no ${pending_file}). The service likely cannot reach central_server, or the
server's signed release data names no relay pairing key. Check:
    curl -fsS -o /dev/null -w '%{http_code}\n' ${MOBAIDE_AGENT_MANIFEST_URL}
    ${log_check}
The service is still running; once the cause is fixed it will initiate a
nonce on its own. Run '$CLI_CMD show-qr' to re-display."
    fi
    info "Rendering pairing QR (scan from the Mobaide mobile app)"
    # --require-pending guards against a double-initiate race with the
    # service's pairing-monitor: the CLI renders the existing file or
    # exits 7, never initiates a second nonce. Two concurrent
    # /ws/pairing/{nonce} dials for the same nonce would race; the
    # central server rejects the loser with WS close 4001.
    # Rendered through the installed command, so a broken shim fails the
    # install instead of the operator's later recovery attempt.
    "$CLI_SHIM" show-qr --require-pending --prefix "$MOBAIDE_AGENT_PREFIX"
}

resolve_run_as_user() {
    # Linux only — macOS installs to ~/ and runs under launchd as the user.
    # Sets script-scoped RUN_USER, RUN_GROUP, RUN_HOME consumed by
    # install_systemd_unit. Not `local`: those are read from a different
    # function frame.
    if [ "$PLATFORM" != "linux" ]; then return 0; fi

    if [ -n "$MOBAIDE_RUN_AS_USER" ]; then
        RUN_USER="$MOBAIDE_RUN_AS_USER"
    elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        RUN_USER="$SUDO_USER"
    else
        fail "Could not determine which user to run the agent as.
The agent manages developer code under /home/<user> and must run as a real
user — not 'root' and not a system account.

Re-run with sudo (so SUDO_USER is set):
    sudo curl -fsSL $MOBAIDE_AGENT_INSTALL_URL | bash

Or pin the user explicitly (e.g. for CI / non-interactive provisioning):
    MOBAIDE_RUN_AS_USER=alice curl -fsSL $MOBAIDE_AGENT_INSTALL_URL | sudo -E bash"
    fi

    if ! id "$RUN_USER" >/dev/null 2>&1; then
        fail "MOBAIDE_RUN_AS_USER='$RUN_USER' does not exist on this host."
    fi
    if [ "$RUN_USER" = "root" ]; then
        fail "Refusing to run the agent as 'root'. Set MOBAIDE_RUN_AS_USER to a regular user."
    fi

    RUN_GROUP="$(id -gn "$RUN_USER")"
    RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
    if [ -z "$RUN_HOME" ]; then
        fail "Could not resolve home directory for user '$RUN_USER'."
    fi
    if [ "$RUN_HOME" = "/" ]; then
        # Renders as `ReadWritePaths=/ <prefix>`, which neutralises
        # ProtectSystem=strict by re-granting writes to the entire root
        # filesystem. Refuse rather than silently weakening the unit.
        fail "Resolved HOME for '$RUN_USER' is '/'; refusing — agent would have writes over the entire root filesystem.
Pick a regular user account: MOBAIDE_RUN_AS_USER=<user>."
    fi

    info "Agent will run as $RUN_USER ($RUN_GROUP), HOME=$RUN_HOME"

    # Best-effort cleanup of the legacy 'mobaide' system user from older
    # installs. Skip if it doesn't exist or is in use; never fail the install.
    if id mobaide >/dev/null 2>&1 && [ "$RUN_USER" != "mobaide" ]; then
        info "Removing legacy 'mobaide' system user from prior installs"
        userdel mobaide >/dev/null 2>&1 || warn "Could not remove legacy 'mobaide' user (still in use?). Continuing."
    fi
}

write_launcher_script() {
    # Generate <prefix>/bin/mobaide-agent-launcher.sh — the supervisor entry
    # point on BOTH Linux (systemd ExecStart) and macOS (launchd
    # ProgramArguments). It resolves the active release and execs its venv
    # Python into ``mobaide.launcher`` (which then owns crash-loop rollback).
    #
    # Self-heal: the in-venv ``mobaide.launcher`` cannot run when the CURRENT
    # release's own interpreter is missing (a half-staged / corrupt release),
    # so the supervisor would crash-loop forever. This wrapper detects that and
    # rolls forward to the newest sibling release that still has a runnable
    # venv BEFORE exec, rewriting current_version so the in-venv launcher takes
    # over cleanly. Runs with the system bash — no dependency on any venv.
    local launcher="$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent-launcher.sh"
    mkdir -p "$MOBAIDE_AGENT_PREFIX/bin"
    # Quoted heredoc: NONE of $prefix/$version/$py is expanded at install time;
    # they resolve at each launch. Intentionally NOT `set -e` — a missing
    # current_version / venv must drive recovery, not abort the script.
    cat > "$launcher" <<'LAUNCHER'
#!/bin/bash
prefix="${MOBAIDE_AGENT_PREFIX:-/usr/local/mobaide}"
version="$(cat "$prefix/current_version" 2>/dev/null || true)"
py="$prefix/releases/$version/venv/bin/python"

# A release is runnable only when fully installed. The updater swaps a pip-less
# venv into place and writes '.installed' only after pip + the wheel land, so
# gate on the sentinel (or, for releases predating it, the mobaide-agent console
# script pip writes last). Keep '.installed' in sync with
# mobaide.agent.install_paths.is_release_complete.
release_complete() {
  [ -f "$1/.installed" ] || [ -e "$1/venv/bin/mobaide-agent" ]
}

# Recover when the current release has no runnable, finalized interpreter.
if [ -z "$version" ] || [ ! -x "$py" ] \
   || ! release_complete "$prefix/releases/$version"; then
  best=""
  for d in "$prefix"/releases/*/; do
    [ -d "$d" ] || continue
    v="$(basename "$d")"
    # Only numeric-dotted release names are recovery candidates — skip dotfile
    # scratch (.staging-*/.old-*) AND any foreign/non-version dir, matching the
    # strict filter the in-venv mobaide.launcher applies to rollback/prune.
    case "$v" in ''|.*|*[!0-9.]*) continue ;; esac
    [ -x "$d/venv/bin/python" ] || continue
    release_complete "$d" || continue
    if [ -z "$best" ]; then
      best="$v"
    else
      # Portable numeric version sort (BSD + GNU); NOT `sort -V` (GNU-only).
      best="$(printf '%s\n%s\n' "$best" "$v" \
        | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -1)"
    fi
  done
  if [ -z "$best" ]; then
    echo "mobaide-agent-launcher: FATAL no runnable release venv under $prefix/releases" >&2
    exit 70
  fi
  if [ "$best" != "$version" ]; then
    printf '%s\n' "$best" > "$prefix/current_version.tmp" \
      && mv "$prefix/current_version.tmp" "$prefix/current_version"
    echo "mobaide-agent-launcher: current release '$version' unrunnable; recovered to '$best'" >&2
  fi
  version="$best"
  py="$prefix/releases/$version/venv/bin/python"
fi
LAUNCHER
    # macOS only: python.org framework Python ships an empty OpenSSL CA store;
    # point OpenSSL at the venv's certifi bundle so wss/HTTPS TLS verifies.
    # Resolved per launch; respects an operator-set SSL_CERT_FILE. NOT emitted
    # on Linux — forcing certifi there could exclude a corporate /etc/ssl root
    # CA and break relay TLS to a self-hosted central server.
    if [ "$PLATFORM" = "darwin" ]; then
        cat >> "$launcher" <<'LAUNCHER_CERTIFI'

if [ -z "${SSL_CERT_FILE:-}" ]; then
  cert="$("$py" -m certifi 2>/dev/null || true)"
  if [ -n "$cert" ]; then
    export SSL_CERT_FILE="$cert"
  fi
fi
LAUNCHER_CERTIFI
    fi
    cat >> "$launcher" <<'LAUNCHER_EXEC'

exec "$py" -m mobaide.launcher
LAUNCHER_EXEC
    chmod +x "$launcher"
}

install_systemd_unit() {
    local unit="/etc/systemd/system/mobaide-agent.service"
    info "Installing systemd unit at $unit (User=$RUN_USER)"
    write_launcher_script
    # ProtectHome is absent because the agent must read/write the user's
    # source tree under $RUN_HOME. ProtectSystem=strict still mounts the
    # rest of the OS hierarchy read-only; ReadWritePaths re-grants writes
    # to exactly what the agent needs ($RUN_HOME for code, the install
    # prefix for releases / agent.env / projects.json). PrivateTmp +
    # NoNewPrivileges complete the residual hardening.
    # StartLimitBurst/IntervalSec give the launcher's 3-strike rollback
    # heuristic room to retry a stable sibling release before systemd
    # gives up; RestartForceExitStatus=75 lets the updater request its
    # own restart.
    cat > "$unit" <<EOF
[Unit]
Description=Mobaide Agent (FastAPI/uvicorn)
After=network.target
Wants=network-online.target
StartLimitBurst=10
StartLimitIntervalSec=600

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$MOBAIDE_AGENT_PREFIX
EnvironmentFile=-$MOBAIDE_AGENT_PREFIX/agent.env
EnvironmentFile=-$MOBAIDE_AGENT_PREFIX/mobaide_agent/.env
Environment=MOBAIDE_AGENT_PREFIX=$MOBAIDE_AGENT_PREFIX
Environment=HOME=$RUN_HOME
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin:%h/.npm-global/bin:%h/.local/bin
ExecStart=$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent-launcher.sh
Restart=on-failure
RestartSec=5
RestartForceExitStatus=75
StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$RUN_HOME $MOBAIDE_AGENT_PREFIX
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
    chown -R "$RUN_USER:$RUN_GROUP" "$MOBAIDE_AGENT_PREFIX"
    install_systemd_uninstall_helper
    systemctl daemon-reload
    systemctl enable mobaide-agent
    systemctl restart mobaide-agent
    if [ -f /etc/systemd/system/mobaide-agent-uninstall.path ]; then
        systemctl enable --now mobaide-agent-uninstall.path
    fi
}

# Root half of the remote uninstall. The agent runs unprivileged under
# NoNewPrivileges, so when the relay orders it to uninstall it can only drop
# <prefix>/run/uninstall-requested; this .path unit sees the marker and runs
# a root-owned script that stops and unregisters the service and deletes the
# install. The script lives outside the user-owned prefix so the run-as user
# cannot rewrite what root executes, and it enters the prefix with `cd -P`
# plus a canonical-path check so a prefix swapped for a symlink is refused.
install_systemd_uninstall_helper() {
    local path_unit="/etc/systemd/system/mobaide-agent-uninstall.path"
    local svc_unit="/etc/systemd/system/mobaide-agent-uninstall.service"
    local script="/usr/local/libexec/mobaide-agent-uninstall"
    local canon marker
    canon="$(readlink -f -- "$MOBAIDE_AGENT_PREFIX" 2>/dev/null || true)"
    case "$canon" in
        ""|/|*"'"*|*" "*)
            warn "Skipping the remote-uninstall helper: unsupported prefix '$MOBAIDE_AGENT_PREFIX'"
            return 0
            ;;
    esac
    marker="$canon/run/uninstall-requested"
    # A marker left over from an earlier uninstall would fire the moment the
    # path unit starts and remove this fresh install.
    rm -f -- "$marker"
    mkdir -p /usr/local/libexec
    # The entry list mirrors PREFIX_ENTRIES in
    # mobaide_agent/mobaide/agent/self_uninstall.py; keep the two in sync.
    cat > "$script" <<EOF
#!/bin/sh
set -u
PREFIX='$canon'
systemctl disable --now mobaide-agent.service mobaide-agent-uninstall.path || true
if cd -P -- "\$PREFIX" 2>/dev/null && [ "\$(pwd -P)" = "\$PREFIX" ]; then
    rm -rf --one-file-system -- releases trust mobaide_agent run state logs svc bin \\
        current_version current_version.tmp .boot_journal agent.env \\
        agent.out.log agent.err.log users.json projects.json acp-adapters \\
        junie-trusted-folders.json autoscan_templates.json autoscan_templates.json.bak \\
        voice voice-fake voice-cleanup voice-cleanup-cursor
    cd /
    rmdir -- "\$PREFIX" 2>/dev/null || true
fi
if grep -qF '$CLI_SHIM_MARKER' $CLI_SHIM_PATH 2>/dev/null; then rm -f $CLI_SHIM_PATH; fi
rm -f $svc_unit $path_unit /etc/systemd/system/mobaide-agent.service
systemctl daemon-reload
rm -f -- "\$0"
EOF
    chown root:root "$script"
    chmod 0755 "$script"
    cat > "$svc_unit" <<EOF
[Unit]
Description=Mobaide Agent remote uninstall

[Service]
Type=oneshot
ExecStart=$script
EOF
    cat > "$path_unit" <<EOF
[Unit]
Description=Watch for a Mobaide Agent remote-uninstall request

[Path]
PathExists=$marker
Unit=mobaide-agent-uninstall.service

[Install]
WantedBy=multi-user.target
EOF
}

resolve_run_as_user_macos() {
    # macOS analog of resolve_run_as_user. The agent manages the developer's
    # own source trees and must run in their GUI (Aqua) session as that user,
    # never as root — so it is loaded as a per-user LaunchAgent in gui/<uid>
    # (see install_launchd_plist). Sets script-scoped RUN_USER/RUN_GROUP/
    # RUN_HOME/RUN_UID (intentionally not `local` — read from other frames).
    if [ "$PLATFORM" != "darwin" ]; then return 0; fi

    if [ -n "$MOBAIDE_RUN_AS_USER" ]; then
        RUN_USER="$MOBAIDE_RUN_AS_USER"
    elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        RUN_USER="$SUDO_USER"
    elif [ "$(id -u)" -ne 0 ]; then
        # Non-root install (user-writable prefix): run as ourselves.
        RUN_USER="$(id -un)"
    else
        # Root shell with no sudo context: fall back to the GUI console owner —
        # whoever is logged into the desktop, which is whose session the agent
        # should live in.
        RUN_USER="$(stat -f '%Su' /dev/console 2>/dev/null || true)"
    fi

    if [ "$RUN_USER" = "root" ]; then
        fail "Refusing to run the agent as 'root'. Re-run with sudo as a regular user,
or pin the user explicitly: MOBAIDE_RUN_AS_USER=<user>."
    fi
    # Reject anything outside the macOS short-name charset before $RUN_USER
    # reaches id/dscl/chown, the tilde `eval`, or the plist — so no shell
    # metacharacter from MOBAIDE_RUN_AS_USER/SUDO_USER/console can flow downstream.
    case "$RUN_USER" in
        ''|*[!A-Za-z0-9._-]*)
            fail "Refusing unsafe agent username '${RUN_USER:-<empty>}' (allowed: A-Za-z0-9._-).
Pin a valid user explicitly: MOBAIDE_RUN_AS_USER=<user>." ;;
    esac
    if ! id "$RUN_USER" >/dev/null 2>&1; then
        fail "Could not determine which macOS user to run the agent as (got '$RUN_USER').
Pin it explicitly: MOBAIDE_RUN_AS_USER=<user>."
    fi

    RUN_UID="$(id -u "$RUN_USER")"
    RUN_GROUP="$(id -gn "$RUN_USER")"
    RUN_HOME="$(dscl . -read "/Users/$RUN_USER" NFSHomeDirectory 2>/dev/null | sed 's/^NFSHomeDirectory: //' || true)"
    if [ -z "$RUN_HOME" ]; then
        RUN_HOME="$(eval echo "~$RUN_USER" 2>/dev/null || true)"
    fi
    if [ -z "$RUN_HOME" ] || [ "$RUN_HOME" = "/" ]; then
        fail "Could not resolve a valid home directory for '$RUN_USER' (got '${RUN_HOME:-<empty>}')."
    fi

    info "Agent will run as $RUN_USER ($RUN_GROUP, uid $RUN_UID), HOME=$RUN_HOME"
}

launchctl_user() {
    # Run launchctl in $RUN_USER's GUI session. From a root context (the common
    # case after auto-elevation) `launchctl asuser <uid>` attaches to the user's
    # Aqua session so gui/<uid> resolves and the loaded job runs as the user.
    # When already running as the user (user-writable prefix), call directly.
    if [ "$(id -u)" -eq 0 ]; then
        launchctl asuser "$RUN_UID" launchctl "$@"
    else
        launchctl "$@"
    fi
}

await_label_unloaded() {
    # Poll until a launchd target (gui/<uid>/<label>) leaves its domain.
    # `launchctl bootout` is asynchronous — the job keeps draining after the
    # command returns — so bootstrapping the same label too soon races the
    # teardown and fails with "Bootstrap failed: 5: Input/output error".
    # Bounded (~5s worst case) so a stuck teardown can never hang the installer.
    local target="$1"
    local i=0
    while launchctl_user print "$target" >/dev/null 2>&1; do
        i=$((i + 1))
        [ "$i" -ge 50 ] && break
        sleep 0.1
    done
}

install_launchd_plist() {
    # Per-user LaunchAgent in the target user's GUI domain (gui/<uid>), loaded
    # via `launchctl bootstrap` as that user — NOT a root `launchctl load`,
    # which warns ("Got LaunchAgents instead") and would run the agent as root.
    local plist="$RUN_HOME/Library/LaunchAgents/com.mobaide.agent.plist"
    local launcher="$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent-launcher.sh"
    local label="com.mobaide.agent"
    local domain="gui/$RUN_UID"
    info "Installing launchd plist at $plist (agent runs as $RUN_USER)"
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$launcher</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MOBAIDE_AGENT_PREFIX</key><string>$MOBAIDE_AGENT_PREFIX</string>
    <key>HOME</key><string>$RUN_HOME</string>
    <!-- launchd does NOT expand \$VAR here, so \$RUN_HOME is substituted at
         install time. /bin and /sbin are required (macOS keeps bash — the
         launcher's #!/bin/bash — and other core tools only there);
         /opt/homebrew/bin and /usr/local/bin cover Apple-Silicon and Intel
         Homebrew where the Claude Code CLI and node typically live. -->
    <key>PATH</key><string>/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin:/sbin:/opt/homebrew/bin:$RUN_HOME/.npm-global/bin:$RUN_HOME/.local/bin</string>
  </dict>
  <!-- The agent's projects.json / users.json store paths are relative
       (AgentConfig defaults) and resolve against the process CWD. launchd
       defaults CWD to / (read-only system volume on modern macOS), so
       without this the agent crashes on its first-boot store write with
       [Errno 30] Read-only file system and never binds 8000. Pin CWD to
       the writable prefix, mirroring the systemd unit's
       WorkingDirectory=\$MOBAIDE_AGENT_PREFIX. -->
  <key>WorkingDirectory</key><string>$MOBAIDE_AGENT_PREFIX</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$MOBAIDE_AGENT_PREFIX/agent.out.log</string>
  <key>StandardErrorPath</key><string>$MOBAIDE_AGENT_PREFIX/agent.err.log</string>
</dict>
</plist>
EOF
    write_launcher_script

    # The agent runs as $RUN_USER, so it must own the prefix (logs, run/,
    # projects) and the plist. Mirrors the Linux chown in install_systemd_unit.
    if [ "$(id -u)" -eq 0 ]; then
        chown -R "$RUN_USER:$RUN_GROUP" "$MOBAIDE_AGENT_PREFIX"
        # Non-recursive on the LaunchAgents dir: fix ownership if mkdir -p just
        # created it as root, without touching the user's other agents' plists.
        chown "$RUN_USER:$RUN_GROUP" "$(dirname "$plist")" "$plist"
    fi

    # bootout first makes re-install idempotent; bootstrap + enable + kickstart
    # load and start the agent in the user's GUI domain. Fall back to the legacy
    # load only if bootstrap keeps failing (e.g. no GUI session on a headless
    # host).
    #
    # `launchctl bootout` is ASYNCHRONOUS: it returns before launchd has finished
    # removing the old job from the domain. Bootstrapping the same label while
    # the previous KeepAlive job is still draining races and fails with
    # "Bootstrap failed: 5: Input/output error" — the re-install-over-a-running-
    # agent case (a clean first install has nothing to drain, so it succeeds).
    # Settle the domain after bootout and retry bootstrap before concluding the
    # host has no GUI session.
    launchctl_user bootout "$domain/$label" 2>/dev/null || true
    await_label_unloaded "$domain/$label"

    local bootstrapped=""
    local attempt=0
    while [ "$attempt" -lt 5 ]; do
        if launchctl_user bootstrap "$domain" "$plist" 2>/dev/null; then
            bootstrapped=1
            break
        fi
        attempt=$((attempt + 1))
        # Residual teardown race: wait for the domain to settle, then retry.
        await_label_unloaded "$domain/$label"
        sleep 1
    done

    if [ -n "$bootstrapped" ]; then
        launchctl_user enable "$domain/$label" 2>/dev/null || true
        launchctl_user kickstart -k "$domain/$label" 2>/dev/null || true
        info "LaunchAgent bootstrapped into $domain."
    else
        warn "Could not bootstrap the agent into $domain after several attempts.
This usually means $RUN_USER has no active GUI (desktop) login session — a
per-user LaunchAgent cannot run without one (e.g. a headless / SSH-only Mac).
Trying a legacy load as a fallback; if that also fails the agent will not start
and the health check below will time out. On a headless host, enable automatic
desktop login for $RUN_USER (or run the agent under a different supervisor)."
        launchctl_user load "$plist" 2>/dev/null || launchctl load "$plist" 2>/dev/null || true
    fi
}

sh_single_quote() {
    # Print $1 as a single-quoted POSIX shell literal ('…', with each
    # embedded ' written as '\'').
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

write_cli_shim() {
    # Generate the `mobaide-agent` command at $1. The release venv's console
    # script moves with every update and rollback, so the shim reads
    # <prefix>/current_version on each call instead of pinning a release.
    # Install-time values are baked in as quoted literals; the quoted heredoc
    # below resolves at call time. Written to a temp file and renamed, so a
    # hand-made symlink at $1 is replaced rather than followed. mktemp creates
    # the temp file exclusively: the target dir can be writable by the agent's
    # user, who could otherwise plant a symlink at a predictable name for
    # root's redirect to follow.
    local target="$1"
    local tmp
    tmp="$(mktemp "$target.XXXXXX")"
    {
        printf '#!/bin/sh\n'
        printf '# %s, written by the Mobaide agent installer.\n' "$CLI_SHIM_MARKER"
        printf 'default_prefix=%s\n' "$(sh_single_quote "$MOBAIDE_AGENT_PREFIX")"
        printf 'run_user=%s\n' "$(sh_single_quote "$RUN_USER")"
        printf 'install_url=%s\n' "$(sh_single_quote "$MOBAIDE_AGENT_INSTALL_URL")"
        cat <<'SHIM'
prefix="${MOBAIDE_AGENT_PREFIX:-$default_prefix}"
version="$(cat "$prefix/current_version" 2>/dev/null)"
cli="$prefix/releases/$version/venv/bin/mobaide-agent"
if [ -z "$version" ] || [ ! -x "$cli" ]; then
    echo "mobaide-agent: no Mobaide agent is installed at $prefix." >&2
    echo "Re-run the installer (curl -fsSL $install_url | bash), or delete this leftover command: sudo rm $0" >&2
    exit 127
fi
# The release venv is writable by the agent's user, so root never runs it.
# Running as that user also keeps every file the CLI writes readable by the
# agent (a root-owned 0600 .env would lock it out).
if [ "$(id -u)" -eq 0 ] && [ -n "$run_user" ] && [ "$run_user" != root ]; then
    if command -v sudo >/dev/null 2>&1; then
        exec sudo -u "$run_user" -H -- env MOBAIDE_AGENT_PREFIX="$prefix" "$cli" "$@"
    fi
    if command -v runuser >/dev/null 2>&1; then
        exec runuser -u "$run_user" -- env MOBAIDE_AGENT_PREFIX="$prefix" "$cli" "$@"
    fi
    echo "mobaide-agent: run this as $run_user, not as root." >&2
    exit 1
fi
MOBAIDE_AGENT_PREFIX="$prefix"
export MOBAIDE_AGENT_PREFIX
exec "$cli" "$@"
SHIM
    } > "$tmp"
    chmod 0755 "$tmp"
    mv -f "$tmp" "$target"
}

cli_shim_target_is_ours() {
    # True when $1 is free, a shim an earlier run wrote, or a hand-made
    # symlink into this install's releases — the usual workaround before the
    # shim existed, pinned to one release that auto-update later prunes.
    local target="$1"
    if [ -L "$target" ]; then
        case "$(readlink "$target")" in
            "$MOBAIDE_AGENT_PREFIX"/releases/*) return 0 ;;
        esac
        return 1
    fi
    [ -e "$target" ] || return 0
    grep -qF "$CLI_SHIM_MARKER" "$target" 2>/dev/null
}

install_cli_shim() {
    # Put `mobaide-agent` on PATH. Sets script-scoped CLI_SHIM (the path the
    # installer runs) and CLI_CMD (what the banners tell the operator to type).
    # A root install owns /usr/local/bin, which every mainstream Linux distro
    # and macOS (/etc/paths) put on PATH, and keeps the shim root-owned. A
    # non-root install (a user-writable prefix on macOS) gets it in
    # <prefix>/bin and is told the full path.
    if [ "$(id -u)" -eq 0 ]; then
        if cli_shim_target_is_ours "$CLI_SHIM_PATH"; then
            mkdir -p "$(dirname "$CLI_SHIM_PATH")"
            write_cli_shim "$CLI_SHIM_PATH"
            CLI_SHIM="$CLI_SHIM_PATH"
            CLI_CMD="mobaide-agent"
            info "Installed the mobaide-agent command at $CLI_SHIM_PATH"
            return 0
        fi
        warn "$CLI_SHIM_PATH exists and was not written by this installer; leaving it alone."
    fi
    CLI_SHIM="$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent"
    mkdir -p "$(dirname "$CLI_SHIM")"
    write_cli_shim "$CLI_SHIM"
    CLI_CMD="$CLI_SHIM"
    info "Installed the mobaide-agent command at $CLI_SHIM (not on PATH; run it by its full path)"
}

print_completion() {
    echo
    echo "============================================"
    echo "  Mobaide agent installed"
    echo "  Prefix:  $MOBAIDE_AGENT_PREFIX"
    echo "  Version: $(cat "$MOBAIDE_AGENT_PREFIX/current_version")"
    if [ "$PLATFORM" = "linux" ]; then
        echo "  Status:  systemctl status mobaide-agent"
        echo "  Logs:    journalctl -u mobaide-agent -f"
    else
        echo "  Logs:    tail -f $MOBAIDE_AGENT_PREFIX/agent.err.log"
    fi
    echo
    echo "  Re-render QR:  $CLI_CMD show-qr"
    echo "  Re-pair:       $CLI_CMD re-pair"
    echo "============================================"
}

main() {
    # Single installer-wide EXIT trap. Cleans up ``$tmpdir``
    # (stage_release's scratch) and ``$BOOTSTRAP_DIR``
    # (resolve_verifier_python's bootstrap venv). stage_release no longer
    # installs its own trap — overriding this one would silently drop the
    # bootstrap-venv cleanup.
    trap cleanup_on_exit EXIT
    detect_platform
    check_macos_version
    require_root_when_writing_system_dir
    info "Installing mobaide_agent into $MOBAIDE_AGENT_PREFIX"
    check_command curl
    check_command tar
    check_python
    ensure_ensurepip_available
    resolve_verifier_python

    mkdir -p "$MOBAIDE_AGENT_PREFIX"
    # Order: choose the trust key (the built-in one, or the operator's) →
    # fetch manifest → verify it against that key → pin the key to disk so
    # the agent picks it up at runtime → stage. A key that does not verify
    # the manifest never replaces the key of an existing install.
    bootstrap_trust_key

    local manifest
    manifest="$(mktemp)"
    fetch_manifest "$manifest"
    verify_manifest_signature "$manifest"
    write_trust_key
    stage_release "$manifest"
    rm -f "$manifest"

    write_agent_env

    # New ordering: bring the agent service up FIRST, then render the
    # QR from the file the running service's pairing-monitor writes.
    # The previous order rendered the QR before the service was up,
    # so a mobile scan during the gap had nothing to dispatch the
    # claim to (the pairing WebSocket is dialed inside the agent
    # process; until uvicorn binds, the central server can't push the
    # signed claim). Mirrors the install.ps1 reorder.
    if [ "$PLATFORM" = "linux" ]; then
        resolve_run_as_user
        install_systemd_unit
    else
        resolve_run_as_user_macos
        install_launchd_plist
    fi
    install_cli_shim

    local health_json
    health_json="$(wait_agent_healthy 60)"
    show_pairing_qr_from_service "$health_json" 60

    print_completion
}

main "$@"
