#!/usr/bin/env python3
"""
E2E test setup script for Actual Budget MCP server.

Spins up an Actual Budget instance with data in ./data/,
builds and starts the MCP server container, seeds test data,
and verifies that the MCP server can retrieve a "TESTING" account.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = PROJECT_DIR / "docker-compose.test.yml"

DEFAULT_ACTUAL_PORT = 5006
DEFAULT_MCP_PORT = 3001

# ANSI colors
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
NC = "\033[0m"


def log(color: str, msg: str) -> None:
    print(f"{color}{msg}{NC}", flush=True)


def compose(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(cmd, check=True, env=env or compose_env)


def wait_for_url(
    url: str,
    max_attempts: int = 30,
    interval: float = 2.0,
    accept_codes: set[int] | None = None,
) -> bool:
    """Poll a URL until it responds (or returns an expected HTTP code)."""
    accept = accept_codes or {200}
    for _ in range(max_attempts):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in accept:
                    return True
        except urllib.error.HTTPError as e:
            if e.code in accept:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(interval)
    return False


def is_port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_container_on_port(port: int) -> str | None:
    """Return the container name occupying a host port, or None."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().splitlines():
        if f":{port}->" in line:
            return line.split("\t")[0]
    return None


def resolve_port(default: int, label: str) -> int:
    """Check if *default* port is free. If occupied by a container, ask the
    user whether to stop it; otherwise fall back to default+1."""
    if not is_port_taken(default):
        return default

    container = find_container_on_port(default)
    if container:
        log(YELLOW, f"  Port {default} is in use by container: {container}")
        try:
            if sys.stdin.isatty():
                answer = input(f"  Stop container '{container}'? [y/N] ").strip().lower()
            else:
                answer = "n"
        except EOFError:
            answer = "n"
        if answer in ("y", "yes"):
            subprocess.run(["docker", "stop", container], check=True)
            log(GREEN, f"  Stopped {container}.")
            time.sleep(1)
            if not is_port_taken(default):
                return default
    else:
        log(YELLOW, f"  Port {default} is in use by a non-Docker process.")

    fallback = default + 1
    if is_port_taken(fallback):
        log(RED, f"  Fallback port {fallback} is also in use. Aborting.")
        sys.exit(1)
    log(YELLOW, f"  Using fallback port {fallback} for {label}.")
    return fallback


def cleanup() -> None:
    log(YELLOW, "\nCleaning up...")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v", "--remove-orphans"],
        capture_output=True,
        env=compose_env,
    )
    tmp_seed = Path("/tmp/actual-e2e-seed")
    if tmp_seed.exists():
        shutil.rmtree(tmp_seed, ignore_errors=True)


# Global env dict for compose – populated in main(), falls back to os.environ
compose_env: dict[str, str] = {**os.environ}


def main() -> None:
    global compose_env

    # Preflight
    for tool in ("docker", "npx"):
        if not shutil.which(tool):
            log(RED, f"{tool} is not installed or not in PATH.")
            sys.exit(1)

    log(YELLOW, "=== E2E Test Setup ===\n")

    # --------------------------------------------------
    # Clean stale data from previous runs
    # --------------------------------------------------
    data_dir = PROJECT_DIR / "data"
    if data_dir.exists():
        log(YELLOW, "  Removing stale ./data/ from previous run...")
        shutil.rmtree(data_dir, ignore_errors=True)

    tmp_seed = Path("/tmp/actual-e2e-seed")
    if tmp_seed.exists():
        shutil.rmtree(tmp_seed, ignore_errors=True)

    # --------------------------------------------------
    # Resolve ports
    # --------------------------------------------------
    actual_port = resolve_port(DEFAULT_ACTUAL_PORT, "Actual Budget server")
    mcp_port = resolve_port(DEFAULT_MCP_PORT, "MCP server")

    # Build a shared env for all compose calls
    compose_env = {
        **os.environ,
        "ACTUAL_HOST_PORT": str(actual_port),
        "MCP_HOST_PORT": str(mcp_port),
        "BUDGET_SYNC_ID": "",  # placeholder until seed
    }

    # --------------------------------------------------
    # Step 1: Start Actual Budget server
    # --------------------------------------------------
    log(YELLOW, "[1/6] Starting Actual Budget server...")
    compose("up", "-d", "actual-server")

    log(YELLOW, "[2/6] Waiting for Actual Budget server to be healthy...")
    if not wait_for_url(f"http://localhost:{actual_port}", max_attempts=30, interval=2):
        log(RED, "  Actual Budget server failed to start within 60s.")
        compose("logs", "actual-server")
        sys.exit(1)
    log(GREEN, "  Actual Budget server is ready.")

    # --------------------------------------------------
    # Step 2: Seed test data
    # --------------------------------------------------
    log(YELLOW, "[3/6] Seeding test data...")
    seed_env = {**os.environ, "ACTUAL_SERVER_URL": f"http://localhost:{actual_port}"}
    result = subprocess.run(
        ["npx", "tsx", "scripts/seed-test-data.ts"],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        env=seed_env,
    )
    if result.returncode != 0:
        log(RED, f"  Seed script failed:\n{result.stderr}")
        sys.exit(1)

    # Reason: @actual-app/api dumps [Breadcrumb] logs to stdout, so extract the
    # last non-empty line which is the sync ID written by process.stdout.write()
    budget_sync_id = ""
    for line in reversed(result.stdout.strip().splitlines()):
        stripped = line.strip()
        if stripped and not stripped.startswith("[") and not stripped.startswith("{"):
            budget_sync_id = stripped
            break
    if not budget_sync_id:
        log(RED, "  Failed to obtain budget sync ID.")
        log(RED, f"  stderr: {result.stderr}")
        sys.exit(1)
    log(GREEN, f"  Budget sync ID: {budget_sync_id}")

    # Update compose env with the real sync ID
    compose_env["BUDGET_SYNC_ID"] = budget_sync_id

    # --------------------------------------------------
    # Step 3: Build & start MCP server
    # --------------------------------------------------
    log(YELLOW, "[4/6] Building and starting MCP server...")
    compose("up", "-d", "--build", "actual-mcp")

    log(YELLOW, "[5/6] Waiting for MCP server to be ready...")
    # /mcp returns 400 for a bare GET (no session) — that means the server is up
    if not wait_for_url(
        f"http://localhost:{mcp_port}/mcp",
        max_attempts=30,
        interval=2,
        accept_codes={200, 400, 405},
    ):
        log(RED, "  MCP server failed to start within 60s.")
        compose("logs", "actual-mcp")
        sys.exit(1)
    log(GREEN, "  MCP server is ready.")

    # --------------------------------------------------
    # Step 4: Test MCP connection
    # --------------------------------------------------
    log(YELLOW, "[6/6] Testing MCP connection (get-accounts -> TESTING)...")
    test_env = {**os.environ, "MCP_URL": f"http://localhost:{mcp_port}/mcp"}
    result = subprocess.run(
        ["npx", "tsx", "scripts/test-mcp-connection.ts"],
        cwd=str(PROJECT_DIR),
        env=test_env,
    )
    if result.returncode != 0:
        log(RED, "  MCP connection test failed.")
        compose("logs", "actual-mcp")
        sys.exit(1)

    log(GREEN, "\n=== All E2E tests passed! ===")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log(YELLOW, "\nInterrupted.")
    except subprocess.CalledProcessError as e:
        log(RED, f"Command failed: {e}")
        sys.exit(1)
    finally:
        cleanup()
