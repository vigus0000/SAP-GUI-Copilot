"""
Bootstrap the local mcp-sap-gui server clone for SAP_Copilot.

Default behavior:
- Clone tingjunchen425/mcp-sap-gui into external/mcp-sap-gui when missing.
- Add/update a `fork` remote when the clone already exists.
- Optionally run `uv sync --extra screenshots` inside the MCP clone.
- Create/update .env with MCP_SAP_SERVER_DIR and local MCP settings.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_REPO_URL = "https://github.com/tingjunchen425/mcp-sap-gui.git"
DEFAULT_TARGET_DIR = ROOT / "external" / "mcp-sap-gui"
DEFAULT_UV_CACHE_DIR = r"C:\tmp\sap-copilot-uv-cache" if os.name == "nt" else "/tmp/sap-copilot-uv-cache"


class SetupError(RuntimeError):
    pass


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    location = f" (cwd={_display_path(cwd)})" if cwd else ""
    print(f"$ {' '.join(cmd)}{location}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise SetupError(f"Required command not found on PATH: {command}")


def git_output(args: list[str], *, cwd: Path) -> str:
    result = run(["git", *args], cwd=cwd, check=True)
    return result.stdout.strip()


def has_dirty_worktree(repo_dir: Path) -> bool:
    result = run(["git", "status", "--porcelain"], cwd=repo_dir, check=True)
    return bool(result.stdout.strip())


def ensure_fork_remote(repo_dir: Path, repo_url: str, *, dry_run: bool) -> None:
    result = run(["git", "remote", "get-url", "fork"], cwd=repo_dir, check=False)
    if result.returncode == 0:
        current = result.stdout.strip()
        if current != repo_url:
            run(["git", "remote", "set-url", "fork", repo_url], cwd=repo_dir, dry_run=dry_run)
            print(f"Updated fork remote: {repo_url}")
        else:
            print(f"fork remote already configured: {repo_url}")
        return

    run(["git", "remote", "add", "fork", repo_url], cwd=repo_dir, dry_run=dry_run)
    print(f"Added fork remote: {repo_url}")


def ensure_mcp_clone(args: argparse.Namespace) -> Path:
    target_dir = args.target.resolve()
    if target_dir.exists():
        if not (target_dir / ".git").exists():
            raise SetupError(f"Target exists but is not a git repo: {_display_path(target_dir)}")

        print(f"MCP clone already exists: {_display_path(target_dir)}")
        ensure_fork_remote(target_dir, args.repo, dry_run=args.dry_run)

        if args.no_update:
            print("Skipped update because --no-update was set.")
            return target_dir

        if has_dirty_worktree(target_dir):
            print("Skipped git pull because the MCP clone has local changes.")
            return target_dir

        branch = git_output(["branch", "--show-current"], cwd=target_dir) or "master"
        run(["git", "fetch", "fork"], cwd=target_dir, dry_run=args.dry_run)
        pull_result = run(
            ["git", "pull", "--ff-only", "fork", branch],
            cwd=target_dir,
            dry_run=args.dry_run,
            check=False,
        )
        if pull_result.returncode != 0:
            print("Could not fast-forward from fork; leaving existing clone unchanged.")
            if pull_result.stderr:
                print(pull_result.stderr.strip())
        return target_dir

    if args.dry_run:
        print(f"Would create directory: {_display_path(target_dir.parent)}")
    else:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", args.repo, str(target_dir)], dry_run=args.dry_run)
    return target_dir


def sync_mcp_dependencies(repo_dir: Path, uv_cache_dir: str, *, dry_run: bool) -> None:
    require_command("uv")
    env = dict(os.environ)
    if uv_cache_dir:
        env.setdefault("UV_CACHE_DIR", uv_cache_dir)
        if not dry_run:
            Path(uv_cache_dir).mkdir(parents=True, exist_ok=True)
    run(["uv", "sync", "--extra", "screenshots"], cwd=repo_dir, env=env, dry_run=dry_run)


def update_env_file(env_path: Path, updates: dict[str, str], *, dry_run: bool) -> None:
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    elif (ROOT / ".env.example").exists():
        print("Creating .env from .env.example")
        lines = (ROOT / ".env.example").read_text(encoding="utf-8-sig").splitlines()
    else:
        print("Creating new .env")
        lines = []

    updated_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue

        key = line.split("=", 1)[0].strip()
        if key in updates:
            updated_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            updated_lines.append(line)

    missing = [key for key in updates if key not in seen]
    if missing:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append("# MCP auto setup")
        for key in missing:
            updated_lines.append(f"{key}={updates[key]}")

    print(f"Updating {_display_path(env_path)}")
    if dry_run:
        return
    env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def build_env_updates(repo_dir: Path, uv_cache_dir: str) -> dict[str, str]:
    return {
        "MCP_SAP_ENABLED": "true",
        "MCP_SAP_SERVER_DIR": _display_path(repo_dir),
        "MCP_SAP_LOCAL_COMMAND": "uv",
        "MCP_SAP_LOCAL_ARGS": "run python -m mcp_sap_gui.server",
        "MCP_SAP_ALLOW_PACKAGE_MODE": "false",
        "MCP_SAP_UV_CACHE_DIR": uv_cache_dir,
        "MCP_SAP_FAST_MODE": "true",
        "MCP_SAP_TOOL_PROFILE": "core",
        "MCP_EXPOSE_DISCOVERY_TO_LLM": "false",
        "MCP_POPUP_USE_POPUP_TOOL_ONLY": "true",
        "MCP_ATTACH_ELEMENTS_AFTER_NAV": "true",
        "MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE": "true",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone/update mcp-sap-gui and configure SAP_Copilot .env."
    )
    parser.add_argument(
        "--repo",
        default=os.getenv("SAP_COPILOT_MCP_REPO", DEFAULT_REPO_URL),
        help=f"MCP git repo URL. Default: {DEFAULT_REPO_URL}",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(os.getenv("SAP_COPILOT_MCP_DIR", str(DEFAULT_TARGET_DIR))),
        help=f"Local MCP clone path. Default: {DEFAULT_TARGET_DIR}",
    )
    parser.add_argument(
        "--uv-cache-dir",
        default=os.getenv("MCP_SAP_UV_CACHE_DIR", DEFAULT_UV_CACHE_DIR),
        help=f"UV cache directory. Default: {DEFAULT_UV_CACHE_DIR}",
    )
    parser.add_argument("--skip-sync", action="store_true", help="Do not run uv sync.")
    parser.add_argument("--no-update", action="store_true", help="Do not pull an existing clone.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        require_command("git")
        repo_dir = ensure_mcp_clone(args)
        if not args.skip_sync:
            sync_mcp_dependencies(repo_dir, args.uv_cache_dir, dry_run=args.dry_run)
        else:
            print("Skipped uv sync because --skip-sync was set.")

        update_env_file(
            ROOT / ".env",
            build_env_updates(repo_dir, args.uv_cache_dir),
            dry_run=args.dry_run,
        )

        print("")
        print("MCP setup complete.")
        print(f"MCP_SAP_SERVER_DIR={_display_path(repo_dir)}")
        print("Run `/mcp` inside SAP_Copilot to verify the MCP server.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {' '.join(exc.cmd)}", file=sys.stderr)
        if exc.stdout:
            print(exc.stdout.strip(), file=sys.stderr)
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return exc.returncode or 1
    except SetupError as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
