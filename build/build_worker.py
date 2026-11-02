# SPDX-License-Identifier: GPL-3.0-only
"""Build the pinned worker with installed Cargo or optional local toolchain config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def cargo_command(env, offline=False):
    """Select installed Cargo or the explicitly configured Windows toolchain."""
    cargo = str(Path(env["RUSTC"]).with_name("cargo.exe")) if env.get("RUSTC") else "cargo"
    return [cargo, "build", "--locked", *(["--offline"] if offline else []),
            "--release", "-p", "vtracer", "--example", "pbn_worker"]


def build_environment(root, inherited, configured=None):
    """Reuse caches, but keep all compiled output inside this checkout."""
    env = dict(inherited)
    env.update(configured or {})
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["CARGO_TARGET_DIR"] = str((root / "native/target").resolve())
    return env


def load_config(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="Use only cached Cargo dependencies (default: locked online build)")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    info = load_config(ROOT / "native/toolchain.json")
    env = build_environment(ROOT, os.environ, info.get("env"))
    command = cargo_command(env, offline=args.offline)
    started = time.perf_counter()
    p = subprocess.run(command, cwd=ROOT / "vendor/vtracer", env=env, capture_output=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/worker-build.log").write_bytes(p.stdout + p.stderr)
    receipt = {"argv": command, "cwd": str(ROOT / "vendor/vtracer"), "exit_code": p.returncode, "elapsed_seconds": time.perf_counter()-started}
    if p.returncode == 0:
        target = ROOT / "assets/worker/pbn_worker.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "native/target/release/examples/pbn_worker.exe", target)
        receipt.update(binary=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest(), bytes=target.stat().st_size)
        receipt.update(adapter_sha256=hashlib.sha256((ROOT / "native/worker/main.rs").read_bytes()).hexdigest(),
                       lock_sha256=hashlib.sha256((ROOT / "vendor/vtracer/Cargo.lock").read_bytes()).hexdigest(),
                       protocols=["one-shot-cli/1", "exact-map-cli/1"])
        (target.parent / "build-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    (ROOT / "reports/worker-build.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    raise SystemExit(p.returncode)

if __name__ == "__main__":
    main()
