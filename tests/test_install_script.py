"""The substrate installer, exercised without touching the network.

`scripts/install-morpholog.sh` is where the pin lives and how the image,
CI and a laptop all get their binary. Two things about it are worth
pinning here, because neither is visible to the jobs that run it: the
destination is resolved before the script moves into a temp directory it
later deletes (a relative destination silently vanished with it), and the
checksum verification has teeth (a download that does not match the pin
must be refused, not installed).

A stub `curl` serves a fabricated release, so no test here needs GitHub
to be reachable. The real download runs in CI's integration leg and the
Docker build on every push, which is the honest place for that proof.
"""

import hashlib
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-morpholog.sh"

# What each machine the release channel covers must resolve to. The
# script selects the artefact by uname, so a wrong row here would be a
# download that cannot run on the machine that asked for it.
TARGETS = {
    ("Linux", "x86_64"): "x86_64-unknown-linux-musl",
    ("Linux", "aarch64"): "aarch64-unknown-linux-musl",
    ("Linux", "arm64"): "aarch64-unknown-linux-musl",
    ("Darwin", "arm64"): "aarch64-apple-darwin",
}


def shim(tmp_path: Path, *, system: str = "Linux", arch: str = "x86_64") -> Path:
    """A PATH holding a `uname` that answers for whatever platform the
    test is pretending to be, plus a `curl` that serves a fabricated
    rolling release (tarball and matching checksum) from disk."""
    path = tmp_path / "shim"
    path.mkdir(exist_ok=True)
    (path / "uname").write_text(f'#!/bin/sh\n[ "$1" = -m ] && echo {arch} || echo {system}\n')

    # The fabricated release is named for the target this platform must
    # select: if the script picks another, curl serves it a tarball whose
    # directory the install step cannot find.
    rolling = f"morpholog-main-{TARGETS.get((system, arch), 'unsupported')}"
    payload = tmp_path / "payload"
    (payload / rolling).mkdir(parents=True, exist_ok=True)
    binary = payload / rolling / "morpholog"
    binary.write_text("#!/bin/sh\necho morpholog-cli 9.9.9-fake\n")
    binary.chmod(0o755)
    tarball = tmp_path / f"{rolling}.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(payload / rolling, arcname=rolling)

    # The real curl's contract, in the two shapes the script uses:
    # `-o <file> <url>` for the tarball and for the checksum beside it.
    # The digest is computed here and embedded as a literal rather than
    # shelled out for: macOS ships `shasum`, not `sha256sum`, and a shim
    # that assumed either one would fail on the very platform half these
    # cases exist to simulate.
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    (path / "curl").write_text(
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -*) shift;; '
        "*) url=$1; shift;; esac; done\n"
        f'case "$url" in\n'
        f'*.sha256) echo "{digest}  $(basename "${{url%.sha256}}")" > "$out" ;;\n'
        f'*) cp "{tarball}" "$out" ;;\n'
        "esac\n"
    )
    for tool in ("uname", "curl"):
        (path / tool).chmod(0o755)
    return path


#: What the script, the shim's own `curl` and the harness that starts
#: them shell out to, so a run can be given a PATH holding exactly these
#: and nothing else.
NEEDED = (
    "sh",
    "mktemp",
    "rm",
    "cp",
    "tar",
    "gzip",
    "mkdir",
    "install",
    "basename",
    "sha256sum",
    "shasum",
)


def without(tmp_path: Path, missing: str) -> Path:
    """A PATH directory carrying every tool the script needs except one,
    so a machine that genuinely lacks it can be simulated rather than
    described. (`command -v` has to find nothing - a stub that exits
    non-zero would still be found, which is the opposite of absent.)"""
    tools = tmp_path / f"tools-no-{missing}"
    tools.mkdir(exist_ok=True)
    for name in NEEDED:
        if name == missing:
            continue
        found = shutil.which(name)
        if found is not None:
            (tools / name).symlink_to(found)
    return tools


def run(
    *args: str, cwd: Path, tools: Path | None = None, **kwargs: str
) -> subprocess.CompletedProcess[str]:
    path = shim(cwd, **kwargs)
    rest = str(tools) if tools is not None else "/usr/bin:/bin"
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{path}:{rest}", "HOME": str(cwd)},
    )


@pytest.mark.parametrize(("system", "arch"), [("Darwin", "x86_64"), ("Linux", "i686")])
def test_an_unpublished_platform_is_refused_by_name(tmp_path: Path, system: str, arch: str) -> None:
    # The release channel covers three machines, not every machine.
    # Refusing by name keeps the narrowing honest: the message carries
    # the remedy (build from source), and the script never downloads a
    # binary that cannot run here. Intel Macs are the live case - GitHub
    # retired the runner, so upstream will not publish an asset no
    # machine of that architecture has executed.
    result = run("out", cwd=tmp_path, system=system, arch=arch)
    assert result.returncode == 2
    assert "no prebuilt morpholog" in result.stderr
    assert "build from source" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(("system", "arch"), list(TARGETS))
def test_every_published_platform_installs_its_own_target(
    tmp_path: Path, system: str, arch: str
) -> None:
    # One test, three machines: the developer laptops upstream started
    # publishing for in v0.0.9 must each get the artefact built for them
    # (the shim only serves the target this platform is meant to ask
    # for), and the destination must survive the temp directory.
    #
    # The relative destination is the other half of the proof: the script
    # works inside a temp directory its exit trap deletes, so a path
    # resolved after that `cd` lands inside it and vanishes on the way
    # out - a silent no-op install.
    result = run(".tools", "main-latest", cwd=tmp_path, system=system, arch=arch)
    assert result.returncode == 0, result.stderr
    installed = tmp_path / ".tools" / "morpholog"
    assert installed.exists()
    assert installed.stat().st_mode & 0o111  # executable, as the callers assume
    assert "9.9.9-fake" in result.stdout  # the script runs what it installed


@pytest.mark.parametrize("absent", ["sha256sum", "shasum"])
def test_the_checksum_is_verified_with_whichever_tool_exists(tmp_path: Path, absent: str) -> None:
    # macOS ships `shasum`, not `sha256sum`, so on the platform this
    # release channel started covering in v0.0.9 the verification takes a
    # branch nothing else here runs. Hiding each tool in turn proves the
    # script needs only one of them, and proves it by running the branch
    # rather than by asserting that it should work.
    result = run(
        ".tools",
        "main-latest",
        cwd=tmp_path,
        system="Darwin",
        arch="arm64",
        tools=without(tmp_path, absent),
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".tools" / "morpholog").exists()


def test_a_download_that_does_not_match_the_pin_is_refused(tmp_path: Path) -> None:
    # The pinned channel checks against the checksum recorded in the
    # script, not one fetched beside the artefact: a served tarball that
    # is not the pinned bytes must be refused, which is the whole reason
    # a mutable tag is not the pin.
    result = run("out", cwd=tmp_path)
    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr  # sha256sum's verdict
    assert not (tmp_path / "out" / "morpholog").exists()


def test_an_unknown_channel_is_refused_before_any_download(tmp_path: Path) -> None:
    result = run("out", "nightly", cwd=tmp_path)
    assert result.returncode == 2
    assert "unknown channel" in result.stderr


def test_the_pin_is_a_version_and_a_checksum_per_target() -> None:
    # The one place the pin lives: if a line is renamed or dropped, a
    # re-pin could quietly install an unverified binary. A pin is
    # per-artefact, so every published target needs its own checksum -
    # there is no single hash the three of them share.
    text = SCRIPT.read_text()
    assert "\nVERSION=v" in text
    assert len({line for line in text.splitlines() if line.startswith("SHA256_")}) == len(
        set(TARGETS.values())
    )
