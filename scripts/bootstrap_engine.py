#!/usr/bin/env python3
"""Build the locked cashflow-rs CLI without modifying its upstream checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / ".engine"
CHECKOUT = ENGINE_DIR / "cashflow-rs"
LOCK_PATH = ROOT / "engine.lock.json"
RUSTUP_INIT_PATH = ENGINE_DIR / "rustup-init"
RUSTUP_BASE_URL = "https://static.rust-lang.org/rustup/dist"


class BootstrapError(RuntimeError):
    pass


def read_lock() -> dict[str, object]:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Cannot read {LOCK_PATH}: {exc}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise BootstrapError(f"Command failed ({completed.returncode}): {args!r}\n{detail}")
    return completed.stdout.strip()


def verify_checkout(lock: dict[str, object]) -> tuple[str, str]:
    if not CHECKOUT.is_dir() or not (CHECKOUT / ".git").exists():
        raise BootstrapError(
            f"Pinned checkout is missing at {CHECKOUT}. Clone the repository from "
            f"{lock.get('repository')} at commit {lock.get('commit')} first."
        )

    commit = run(["git", "rev-parse", "HEAD"], cwd=CHECKOUT)
    expected_commit = str(lock.get("commit", ""))
    if commit != expected_commit:
        raise BootstrapError(
            f"Upstream checkout is at {commit}, expected {expected_commit}; "
            "bootstrap never checks out or updates upstream automatically."
        )

    # Comparing HEAD with the index and worktree checks tracked files without
    # rejecting ignored build outputs or untracked upstream files.
    run(["git", "diff", "--quiet", "HEAD", "--"], cwd=CHECKOUT)
    cargo_lock = CHECKOUT / "Cargo.lock"
    if not cargo_lock.is_file():
        raise BootstrapError(f"Pinned checkout has no Cargo.lock: {cargo_lock}")
    lock_hash = sha256(cargo_lock)
    expected_hash = lock.get("cargo_lock_sha256")
    if expected_hash and lock_hash != expected_hash:
        raise BootstrapError(
            f"Cargo.lock SHA-256 is {lock_hash}, expected {expected_hash}; "
            "do not edit the pinned upstream checkout."
        )
    return commit, lock_hash


def ensure_checkout(lock: dict[str, object]) -> None:
    """Clone only when absent; never move an existing checkout's HEAD."""
    if CHECKOUT.exists():
        return
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    repository = str(lock.get("repository", ""))
    commit = str(lock.get("commit", ""))
    if not repository or not commit:
        raise BootstrapError("engine.lock.json must define repository and commit.")
    run(["git", "clone", "--no-checkout", repository, str(CHECKOUT)], cwd=ROOT)
    run(["git", "-C", str(CHECKOUT), "fetch", "origin", commit], cwd=ROOT)
    run(["git", "-C", str(CHECKOUT), "checkout", "--detach", commit], cwd=ROOT)


def rustup_host() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    hosts = {
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Darwin", "aarch64"): "aarch64-apple-darwin",
        ("Darwin", "x86_64"): "x86_64-apple-darwin",
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
    }
    try:
        return hosts[(system, machine)]
    except KeyError as exc:
        raise BootstrapError(
            f"No pinned rustup-init download target for {system}/{machine}."
        ) from exc


def install_local_rust(toolchain: str) -> Path:
    cargo = ENGINE_DIR / "cargo" / "bin" / "cargo"
    rustup = ENGINE_DIR / "cargo" / "bin" / "rustup"
    env = os.environ.copy()
    env["RUSTUP_HOME"] = str(ENGINE_DIR / "rustup")
    env["CARGO_HOME"] = str(ENGINE_DIR / "cargo")
    env["PATH"] = str(cargo.parent) + os.pathsep + env.get("PATH", "")

    if cargo.is_file() and rustup.is_file():
        rustc = cargo.parent / "rustc"
        if rustc.is_file() and run([str(rustc), "--version"], cwd=ROOT, env=env).startswith(
            f"rustc {toolchain} "
        ):
            return cargo
        run([str(rustup), "toolchain", "install", toolchain, "--profile", "minimal"], cwd=ROOT, env=env)
        run([str(rustup), "default", toolchain], cwd=ROOT, env=env)
        return cargo

    host = rustup_host()
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{RUSTUP_BASE_URL}/{host}/rustup-init"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "factor-lab-engine-bootstrap/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except OSError as exc:
        raise BootstrapError(f"Could not download official rustup-init from {url}: {exc}") from exc

    RUSTUP_INIT_PATH.write_bytes(data)
    RUSTUP_INIT_PATH.chmod(0o755)
    run(
        [
            str(RUSTUP_INIT_PATH),
            "--default-toolchain",
            toolchain,
            "--profile",
            "minimal",
            "--no-modify-path",
            "-y",
        ],
        cwd=ROOT,
        env=env,
    )
    if not cargo.is_file():
        raise BootstrapError(f"rustup completed but local cargo was not created at {cargo}")
    return cargo


def select_cargo(install_rust: bool, toolchain: str) -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    if install_rust:
        cargo = install_local_rust(toolchain)
        cargo_bin = cargo.parent
        env["RUSTUP_HOME"] = str(ENGINE_DIR / "rustup")
        env["CARGO_HOME"] = str(ENGINE_DIR / "cargo")
        env["PATH"] = str(cargo_bin) + os.pathsep + env.get("PATH", "")
        return str(cargo), env

    local_cargo = ENGINE_DIR / "cargo" / "bin" / "cargo"
    if local_cargo.is_file():
        env["RUSTUP_HOME"] = str(ENGINE_DIR / "rustup")
        env["CARGO_HOME"] = str(ENGINE_DIR / "cargo")
        env["PATH"] = str(local_cargo.parent) + os.pathsep + env.get("PATH", "")
        return str(local_cargo), env

    system_cargo = shutil.which("cargo")
    if system_cargo:
        return system_cargo, env

    raise BootstrapError(
        "Cargo was not found. Install the pinned local Rust toolchain with "
        "`python3 scripts/bootstrap_engine.py --install-rust`."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-rust",
        action="store_true",
        help="download official rustup-init and install the pinned toolchain under .engine/",
    )
    args = parser.parse_args()

    try:
        lock = read_lock()
        toolchain = str(lock.get("rust_toolchain", "1.90.0"))
        ensure_checkout(lock)
        commit, lock_hash_before = verify_checkout(lock)
        cargo, env = select_cargo(args.install_rust, toolchain)
        rustc = shutil.which("rustc", path=env.get("PATH"))
        if not rustc:
            raise BootstrapError("rustc was not found alongside Cargo; install Rust with --install-rust.")
        rust_version = run([rustc, "--version"], cwd=CHECKOUT, env=env)
        cargo_version = run([cargo, "--version"], cwd=CHECKOUT, env=env)
        if not rust_version.startswith(f"rustc {toolchain} "):
            raise BootstrapError(
                f"Expected pinned Rust {toolchain}, got {rust_version}. "
                "Install the pinned local toolchain with `python3 scripts/bootstrap_engine.py --install-rust`."
            )

        build_command = [cargo, "build", "--locked", "--release", "--bin", str(lock["binary"])]
        run(build_command, cwd=CHECKOUT, env=env)

        lock_hash_after = sha256(CHECKOUT / "Cargo.lock")
        if lock_hash_after != lock_hash_before:
            raise BootstrapError("Cargo.lock changed during the build; refusing to accept the artifact.")
        verify_checkout(lock)

        binary = CHECKOUT / "target" / "release" / str(lock["binary"])
        if not binary.is_file():
            raise BootstrapError(f"Build succeeded but expected CLI is missing: {binary}")
        info = {
            "commit": commit,
            "cargo_lock_sha256": lock_hash_after,
            "binary_sha256": sha256(binary),
            "version": lock.get("package_version"),
            "rust_version": rust_version,
            "cargo_version": cargo_version,
            "binary": str(binary.relative_to(ROOT)),
        }
        info_path = ENGINE_DIR / "build-info.json"
        info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        print(f"Built {binary}")
        print(f"Build metadata: {info_path}")
        print(f"Cargo.lock SHA-256: {lock_hash_after}")
        print(f"{rust_version}; {cargo_version}")
        return 0
    except (BootstrapError, KeyError) as exc:
        print(f"bootstrap_engine: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
