import json
import subprocess
import sys
from pathlib import Path

import pytest


def run(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "deltasync.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def _write_basis_and_new(workdir: Path):
    rng_bytes = bytes((i * 31 + 3) % 256 for i in range(20000))
    basis = workdir / "basis.bin"
    basis.write_bytes(rng_bytes)
    new = workdir / "new.bin"
    # basis with a chunk replaced and something appended
    edited = rng_bytes[:8000] + b"EDITED SECTION " * 50 + rng_bytes[9000:]
    new.write_bytes(edited + b"\nappended tail")
    return basis, new


def test_signature_delta_patch_round_trip_via_subprocess(workdir):
    basis, new = _write_basis_and_new(workdir)
    sig_path = workdir / "sig.json"
    delta_path = workdir / "delta.bin"
    out_path = workdir / "out.bin"

    r1 = run("signature", str(basis), "-o", str(sig_path), "--block-size", "512", cwd=workdir)
    assert r1.returncode == 0, r1.stderr

    sig_data = json.loads(sig_path.read_text())
    assert sig_data["block_size"] == 512
    assert len(sig_data["blocks"]) > 0

    r2 = run("delta", str(new), str(sig_path), "-o", str(delta_path), cwd=workdir)
    assert r2.returncode == 0, r2.stderr
    assert delta_path.stat().st_size > 0

    r3 = run(
        "patch", str(basis), str(delta_path), "-o", str(out_path), "--verify", str(new), cwd=workdir
    )
    assert r3.returncode == 0, r3.stderr
    assert "verify: OK" in r3.stdout
    assert out_path.read_bytes() == new.read_bytes()


def test_diff_command_reports_ok_round_trip(workdir):
    basis, new = _write_basis_and_new(workdir)
    r = run("diff", str(basis), str(new), "--block-size", "512", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "round-trip check:   OK" in r.stdout


def test_patch_reports_mismatch_and_exits_nonzero_on_wrong_verify_target(workdir):
    basis, new = _write_basis_and_new(workdir)
    sig_path = workdir / "sig.json"
    delta_path = workdir / "delta.bin"
    out_path = workdir / "out.bin"
    wrong = workdir / "wrong.bin"
    wrong.write_bytes(b"totally different contents")

    run("signature", str(basis), "-o", str(sig_path), cwd=workdir)
    run("delta", str(new), str(sig_path), "-o", str(delta_path), cwd=workdir)
    r = run("patch", str(basis), str(delta_path), "-o", str(out_path), "--verify", str(wrong), cwd=workdir)

    assert r.returncode == 1
    # the CLI intentionally writes the MISMATCH line to stderr (it's an
    # error condition), not stdout
    assert "MISMATCH" in r.stderr


def test_patch_rejects_corrupted_delta_file(workdir):
    basis, new = _write_basis_and_new(workdir)
    sig_path = workdir / "sig.json"
    delta_path = workdir / "delta.bin"
    out_path = workdir / "out.bin"

    run("signature", str(basis), "-o", str(sig_path), cwd=workdir)
    run("delta", str(new), str(sig_path), "-o", str(delta_path), cwd=workdir)

    corrupted = bytearray(delta_path.read_bytes())
    corrupted[len(corrupted) // 2] ^= 0xFF
    delta_path.write_bytes(bytes(corrupted))

    r = run("patch", str(basis), str(delta_path), "-o", str(out_path), cwd=workdir)
    assert r.returncode == 1
    assert "error:" in r.stderr.lower()
