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

import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-morpholog.sh"
ROLLING = "morpholog-main-x86_64-unknown-linux-musl"


def shim(tmp_path: Path, *, system: str = "Linux", arch: str = "x86_64") -> Path:
    """A PATH holding a `uname` that answers for whatever platform the
    test is pretending to be, plus a `curl` that serves a fabricated
    rolling release (tarball and matching checksum) from disk."""
    path = tmp_path / "shim"
    path.mkdir(exist_ok=True)
    (path / "uname").write_text(f'#!/bin/sh\n[ "$1" = -m ] && echo {arch} || echo {system}\n')

    payload = tmp_path / "payload"
    (payload / ROLLING).mkdir(parents=True, exist_ok=True)
    binary = payload / ROLLING / "morpholog"
    binary.write_text("#!/bin/sh\necho morpholog-cli 9.9.9-fake\n")
    binary.chmod(0o755)
    tarball = tmp_path / f"{ROLLING}.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(payload / ROLLING, arcname=ROLLING)

    # The real curl's contract, in the two shapes the script uses:
    # `-o <file> <url>` for the tarball and for the checksum beside it.
    (path / "curl").write_text(
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -*) shift;; '
        "*) url=$1; shift;; esac; done\n"
        f'case "$url" in\n'
        f'*.sha256) echo "$(sha256sum "{tarball}" | cut -d" " -f1)  $(basename "${{url%.sha256}}")"'
        ' > "$out" ;;\n'
        f'*) cp "{tarball}" "$out" ;;\n'
        "esac\n"
    )
    for tool in ("uname", "curl"):
        (path / tool).chmod(0o755)
    return path


def run(*args: str, cwd: Path, **kwargs: str) -> subprocess.CompletedProcess[str]:
    path = shim(cwd, **kwargs)
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{path}:/usr/bin:/bin", "HOME": str(cwd)},
    )


@pytest.mark.parametrize(
    ("system", "arch"), [("Darwin", "arm64"), ("Linux", "aarch64"), ("Darwin", "x86_64")]
)
def test_an_unsupported_platform_is_refused_by_name(tmp_path: Path, system: str, arch: str) -> None:
    # Upstream publishes one target. Refusing by name keeps the narrowing
    # honest: the message carries the remedy (build from source), and the
    # script never downloads a binary that cannot run here.
    result = run("out", cwd=tmp_path, system=system, arch=arch)
    assert result.returncode == 2
    assert "no prebuilt morpholog" in result.stderr
    assert "build from source" in result.stderr
    assert not (tmp_path / "out").exists()


def test_a_relative_destination_survives_the_temp_directory(tmp_path: Path) -> None:
    # The script works inside a temp directory its exit trap deletes, so
    # a destination resolved after that `cd` lands inside it and vanishes
    # on the way out - a silent no-op install.
    result = run(".tools", "main-latest", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    installed = tmp_path / ".tools" / "morpholog"
    assert installed.exists()
    assert installed.stat().st_mode & 0o111  # executable, as the callers assume
    assert "9.9.9-fake" in result.stdout  # the script runs what it installed


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


def test_the_pin_is_a_version_and_a_checksum() -> None:
    # The one place the pin lives: if either line is renamed or dropped,
    # a re-pin could quietly install an unverified binary.
    text = SCRIPT.read_text()
    assert "\nVERSION=v" in text
    assert "\nSHA256=" in text
