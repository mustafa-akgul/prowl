"""Review Agent — application entry point.

Starts the FastAPI web panel via uvicorn.
Dashboard: http://localhost:8080

Usage:
    python main.py               # default port 8080
    python main.py --port 3000
    PORT=3000 python main.py
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn
from dotenv import load_dotenv

from agent.config_manager import get_api_config, mask_sensitive

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def _check_config(api: dict[str, str] | None = None) -> list[str]:
    """Checks whether required environment variables are configured.

    Args:
        api: Pre-loaded API config dict. Loaded from disk if not provided.

    Returns:
        List of missing variable labels.
    """
    if api is None:
        api = get_api_config()
    required = {
        "gemini_api_key": "GEMINI_API_KEY",
        "github_token": "GITHUB_TOKEN",
        "github_repo": "GITHUB_REPO",
    }
    return [label for key, label in required.items() if not api.get(key)]


def _print_banner(port: int, api: dict[str, str] | None = None) -> None:
    """Prints the startup banner and service status summary.

    Args:
        port: Port the server is listening on.
        api: Pre-loaded API config dict. Loaded from disk if not provided.
    """
    if api is None:
        api = get_api_config()

    def status(key: str) -> str:
        return "✓" if api.get(key) else "✗ (missing)"

    gemini_display = mask_sensitive(api.get("gemini_api_key", "")) or "— not configured"
    github_repo = api.get("github_repo", "") or "— not configured"
    jira_url = api.get("jira_url", "") or "— not configured"

    print(
        f"""
╔══════════════════════════════════════════════╗
║      PRowl — AI Code Review Agent  v3.1      ║
╚══════════════════════════════════════════════╝

  Dashboard  →  http://localhost:{port}
  Settings   →  http://localhost:{port}/settings
  Prompts    →  http://localhost:{port}/prompts
  Board      →  http://localhost:{port}/board

  Service Status
  ─────────────────────────────────────────────
  Gemini API  {status("gemini_api_key")}  {gemini_display}
  GitHub      {status("github_token")}  {github_repo}
  Jira        {status("jira_url")}  {jira_url}

"""
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses CLI arguments.

    Args:
        argv: Command-line arguments (overrides sys.argv in tests).

    Returns:
        Parsed argument namespace.
    """
    parser = argparse.ArgumentParser(description="Start the Review Agent web panel.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8080")),
        help="Server port (default: 8080, override with PORT env var)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("HOST", "0.0.0.0"),
        help="Server host address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable hot-reload for development",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    args = _parse_args(argv)

    # Read config once and share between check and banner
    api = get_api_config()

    missing = _check_config(api)
    if missing:
        logger.warning(
            "The following variables are not configured: %s — "
            "you can set them at http://localhost:%d/settings",
            ", ".join(missing),
            args.port,
        )

    _print_banner(args.port, api)

    uvicorn.run(
        "agent.dashboard:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
