# SPDX-License-Identifier: GPL-3.0-only
"""Public build configuration needs no private machine paths."""
from pathlib import Path

from build import build_worker


def test_standard_cargo_build_is_locked_online():
    assert build_worker.cargo_command({}) == [
        "cargo", "build", "--locked", "--release", "-p", "vtracer",
        "--example", "pbn_worker",
    ]


def test_offline_is_explicit():
    command = build_worker.cargo_command({}, offline=True)
    assert command == ["cargo", "build", "--locked", "--offline", "--release",
                       "-p", "vtracer", "--example", "pbn_worker"]


def test_configured_compiler_selects_sibling_cargo():
    rustc = Path("toolchain") / "bin" / "rustc.exe"
    assert build_worker.cargo_command({"RUSTC": str(rustc)})[0] == str(
        rustc.with_name("cargo.exe"))


def test_standard_environment_preserves_cargo_home_and_cleans_python():
    inherited = {"PATH": "installed-tools", "CARGO_HOME": "cache",
                 "PYTHONHOME": "bad", "PYTHONPATH": "bad"}
    original = inherited.copy()
    env = build_worker.build_environment(Path("checkout"), inherited)
    assert env == {"PATH": "installed-tools", "CARGO_HOME": "cache",
                   "CARGO_TARGET_DIR": str(Path("checkout/native/target").resolve())}
    assert inherited == original


def test_local_config_overrides_tools_but_never_writes_private_target():
    configured = {"RUSTC": "local/rustc.exe", "CARGO_HOME": "shared-cache",
                  "CARGO_TARGET_DIR": "private-target", "LIB": "sdk",
                  "PYTHONPATH": "bad", "PYTHONHOME": "bad"}
    original = configured.copy()
    env = build_worker.build_environment(Path("public"), {"PATH": "tools"}, configured)
    assert env == {"PATH": "tools", "RUSTC": "local/rustc.exe",
                   "CARGO_HOME": "shared-cache", "LIB": "sdk",
                   "CARGO_TARGET_DIR": str(Path("public/native/target").resolve())}
    assert configured == original


def test_cli_defaults_online_and_accepts_offline():
    assert build_worker.parse_args([]).offline is False
    assert build_worker.parse_args(["--offline"]).offline is True


def test_missing_local_toolchain_config_is_optional(tmp_path):
    assert build_worker.load_config(tmp_path / "toolchain.json") == {}


def test_local_toolchain_config_is_loaded(tmp_path):
    config = tmp_path / "toolchain.json"
    config.write_text('{"env": {"CARGO_HOME": "cache"}}', encoding="utf-8")
    assert build_worker.load_config(config) == {"env": {"CARGO_HOME": "cache"}}


def test_standard_environment_leaves_default_cargo_cache_to_cargo():
    assert "CARGO_HOME" not in build_worker.build_environment(Path("public"), {})
