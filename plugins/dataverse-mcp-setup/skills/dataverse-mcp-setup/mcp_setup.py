#!/usr/bin/env python3
"""
mcp_setup.py — Unified MCP server configuration tool for Dataverse.

This script consolidates the configuration, listing, and detection of Dataverse
MCP servers for both Claude Code CLI and GitHub Copilot.

Commands:
  list-environments    List all Dataverse environments accessible to the user
  get-configured       Get a JSON array of already-configured MCP server URLs
  configure            Configure the Dataverse MCP server for available tools
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# Constants
SERVER_NAME = "Dataverse"
CLAUDE_OAUTH_CLIENT_ID = "f4ffe97c-20c7-4620-9ad6-b3e41f878dd0"


def get_copilot_config_path() -> Tuple[Path, str]:
    """Get the Copilot config file path and display path."""
    is_windows = platform.system() == "Windows"

    if is_windows:
        home = os.environ.get("USERPROFILE", os.path.expanduser("~"))
        display_path = r"%USERPROFILE%\.copilot\mcp-config.json"
    else:
        home = os.path.expanduser("~")
        display_path = "~/.copilot/mcp-config.json"

    config_dir = Path(home) / ".copilot"
    config_file = config_dir / "mcp-config.json"

    return config_file, display_path


def has_claude_cli() -> bool:
    """Check if Claude CLI is available."""
    return shutil.which("claude") is not None


def detect_tools() -> int:
    """
    Detect which tools are available on the system.

    Returns:
        Exit code (always 0)

    Outputs JSON with available tools.
    """
    tools = {
        "claude": has_claude_cli(),
        "copilot": True  # If we're running Python, Copilot can be configured
    }
    print(json.dumps(tools, indent=2))
    return 0


def list_environments() -> int:
    """
    List Dataverse environments accessible to the current user.

    Returns:
        Exit code (0 on success, non-zero on failure)
    """
    # Check for Azure CLI
    if not shutil.which("az"):
        print(
            "Error: Azure CLI (az) is not installed. "
            "Install it from https://aka.ms/installazurecli",
            file=sys.stderr
        )
        return 1

    # On Windows, we need shell=True to execute .cmd files
    use_shell = platform.system() == "Windows"

    # Check if logged in, prompt login if needed
    try:
        subprocess.run(
            ["az", "account", "show"],
            capture_output=True,
            check=True,
            timeout=10,
            shell=use_shell
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        try:
            subprocess.run(["az", "login"], check=True, shell=use_shell)
        except subprocess.CalledProcessError:
            print("Error: Azure login failed.", file=sys.stderr)
            return 1

    # Get access token
    try:
        result = subprocess.run(
            [
                "az", "account", "get-access-token",
                "--resource", "https://service.powerapps.com/",
                "--query", "accessToken",
                "--output", "tsv"
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            shell=use_shell
        )
        token = result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(
            "Error: Could not obtain an access token. "
            "Run 'az login' and try again.",
            file=sys.stderr
        )
        return 1

    # Call Power Apps API (or TIP if environment variable is set)
    use_tip = os.environ.get("MCP_SETUP_USETIP", "").lower() in ("true", "1", "yes")

    if use_tip:
        url = "https://tip1.api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/scopes/admin/environments?api-version=2023-06-01"
    else:
        url = "https://api.powerapps.com/providers/Microsoft.PowerApps/environments?api-version=2016-11-01"

    try:
        request = Request(url)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/json")

        with urlopen(request, timeout=30) as response:
            response_data = response.read().decode("utf-8")
            data = json.loads(response_data)
    except HTTPError as e:
        print(f"Error: HTTP {e.code} - Failed to retrieve environments from Power Apps API.", file=sys.stderr)
        return 1
    except URLError as e:
        print(f"Error: Failed to connect to Power Apps API: {e.reason}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse API response: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: Unexpected error calling Power Apps API: {e}", file=sys.stderr)
        return 1

    # Filter for Dataverse environments
    envs = []
    for e in data.get("value", []):
        props = e.get("properties", {})
        if props.get("databaseType") != "CommonDataService":
            continue

        linked_metadata = props.get("linkedEnvironmentMetadata", {})
        instance_url = linked_metadata.get("instanceUrl", "").rstrip("/")

        if instance_url:
            envs.append({
                "displayName": props.get("displayName", "(unnamed)"),
                "instanceUrl": instance_url
            })

    if not envs:
        print(
            "No Dataverse (CommonDataService) environments were found for this account.",
            file=sys.stderr
        )
        return 1

    print(json.dumps(envs, indent=2))
    return 0


def get_configured_servers(tool: str = "both") -> int:
    """
    Get a JSON array of already-configured MCP server URLs.

    Args:
        tool: Which tool to check - "claude", "copilot", or "both"

    Returns:
        Exit code (always 0)
    """
    urls: Set[str] = set()
    use_shell = platform.system() == "Windows"

    # Source 1: claude mcp list
    if tool in ("claude", "both") and has_claude_cli():
        try:
            result = subprocess.run(
                ["claude", "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=use_shell
            )
            if result.returncode == 0:
                for url in re.findall(r"https://\S+", result.stdout):
                    cleaned = url.rstrip(".,;)\"'")
                    urls.add(cleaned)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Source 2: mcp-config.json
    if tool in ("copilot", "both"):
        config_file, _ = get_copilot_config_path()
        try:
            with open(config_file, encoding="utf-8") as f:
                config = json.load(f)

            # Check both "mcpServers" and "servers" keys
            servers = config.get("mcpServers", config.get("servers", {}))
            for server in servers.values():
                url = server.get("url", "").rstrip("/")
                if url.startswith("https://"):
                    urls.add(url)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

    print(json.dumps(sorted(urls)))
    return 0


def configure_copilot(mcp_url: str) -> None:
    """
    Configure GitHub Copilot by updating mcp-config.json.

    Args:
        mcp_url: The MCP server URL to configure
    """
    config_file, _ = get_copilot_config_path()

    # Ensure directory exists
    config_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or create new
    try:
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}

    # Use "servers" if present, otherwise "mcpServers"
    servers_key = "servers" if "servers" in config else "mcpServers"
    if servers_key not in config:
        config[servers_key] = {}

    # Add or update Dataverse entry (no OAuth client ID needed for Copilot)
    config[servers_key][SERVER_NAME] = {
        "type": "http",
        "url": mcp_url,
    }

    # Write back
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def configure_claude(mcp_url: str) -> Tuple[bool, Optional[str]]:
    """
    Configure Claude Code CLI.

    Args:
        mcp_url: The MCP server URL to configure

    Returns:
        Tuple of (success, manual_command)
        - success: True if configured successfully, False if manual intervention needed
        - manual_command: The command to run manually if success is False, None otherwise
    """
    use_shell = platform.system() == "Windows"

    try:
        result = subprocess.run(
            [
                "claude", "mcp", "add",
                "--scope", "user",
                SERVER_NAME,
                "-t", "http",
                mcp_url,
                "--client-id", CLAUDE_OAUTH_CLIENT_ID
            ],
            check=True,
            shell=use_shell,
            capture_output=True,
            text=True
        )
        return (True, None)
    except subprocess.CalledProcessError as e:
        # Check if this is the "nested Claude session" error
        error_output = e.stderr + e.stdout
        if "cannot be launched inside another Claude Code session" in error_output.lower() or \
           "already running" in error_output.lower():
            # Return the manual command
            manual_cmd = (
                f'claude mcp add --scope user "{SERVER_NAME}" -t http "{mcp_url}" '
                f'--client-id "{CLAUDE_OAUTH_CLIENT_ID}"'
            )
            return (False, manual_cmd)
        # Re-raise if it's a different error
        raise


def print_manual_instructions(mcp_url: str, display_path: str) -> None:
    """
    Print manual configuration instructions when neither tool is available.

    Args:
        mcp_url: The MCP server URL to configure
        display_path: Display-friendly path to config file
    """
    print(f"""
Neither the Claude Code CLI nor Python is installed, so the config cannot be
updated automatically.

To register the Dataverse MCP server, choose one of the following options:

── Option A: Claude Code CLI ──────────────────────────────────────────────────
Once the Claude Code CLI is installed, run:

  claude mcp add --scope user "{SERVER_NAME}" -t http "{mcp_url}" \\
    --client-id "{OAUTH_CLIENT_ID}"

── Option B: GitHub Copilot (manual file edit) ────────────────────────────────
Open (or create) this file:

  {display_path}

Add the "Dataverse" entry under "mcpServers". If the file does not exist yet,
create it with exactly this content:

{{
  "mcpServers": {{
    "Dataverse": {{
      "command": "http",
      "url": "{mcp_url}",
      "oauthClientId": "{OAUTH_CLIENT_ID}",
      "oauthPublicClient": true
    }}
  }}
}}

If the file already contains other entries under "mcpServers", keep them and
add the "Dataverse" block alongside them without removing existing entries.

Save the file, then restart your agent or reload its configuration.
""")


def configure(org_url: str, endpoint_type: str = "ga", tool: str = "both") -> int:
    """
    Configure the Dataverse MCP server.

    Args:
        org_url: Dataverse environment URL (e.g., https://myorg.crm.dynamics.com)
        endpoint_type: "preview" or "ga" (default: ga)
        tool: Which tool to configure - "claude", "copilot", or "both"

    Returns:
        Exit code (0 on success, 1 on error)
    """
    # Build MCP URL
    if endpoint_type == "preview":
        mcp_url = f"{org_url}/api/mcp_preview"
    else:
        mcp_url = f"{org_url}/api/mcp"

    # Detect available tools
    has_claude = has_claude_cli()
    config_file, display_path = get_copilot_config_path()

    configured_for: List[str] = []
    manual_commands: List[str] = []

    # Configure Claude CLI if requested and available
    if tool in ("claude", "both"):
        if not has_claude:
            print(f"Error: Claude CLI not found. Cannot configure Claude.", file=sys.stderr)
            if tool == "claude":  # Only fail if exclusively requested
                return 1
        else:
            try:
                success, manual_cmd = configure_claude(mcp_url)
                if success:
                    configured_for.append("Claude Code CLI")
                else:
                    # Claude is running inside another Claude session
                    manual_commands.append(
                        f"\nClaude Code cannot be configured from within a Claude session.\n"
                        f"Please exit this Claude session and run the following command:\n\n"
                        f"  {manual_cmd}\n"
                    )
                    if tool == "claude":  # If exclusively requested, we still "succeeded" but need manual step
                        print(manual_commands[0])
                        return 0
            except subprocess.CalledProcessError as e:
                print(f"Error: Failed to configure Claude CLI: {e}", file=sys.stderr)
                if tool == "claude":  # Only fail if exclusively requested
                    return 1

    # Configure Copilot if requested
    if tool in ("copilot", "both"):
        try:
            configure_copilot(mcp_url)
            configured_for.append("GitHub Copilot")
        except Exception as e:
            print(f"Error: Failed to configure GitHub Copilot: {e}", file=sys.stderr)
            if tool == "copilot":  # Only fail if exclusively requested
                return 1

    if not configured_for and not manual_commands:
        print("Error: No tools were configured.", file=sys.stderr)
        return 1

    # Print success message
    if configured_for:
        print("Dataverse MCP server registered successfully.")
        print(f"  Server URL     : {mcp_url}")
        print(f"  Configured for : {', '.join(configured_for)}")
        if "GitHub Copilot" in configured_for:
            print(f"  Copilot config : {config_file}")

    # Print manual commands if needed
    if manual_commands:
        print("\n" + "=" * 80)
        print("MANUAL CONFIGURATION REQUIRED")
        print("=" * 80)
        for cmd in manual_commands:
            print(cmd)

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Unified MCP server configuration tool for Dataverse",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # detect-tools command
    subparsers.add_parser(
        "detect-tools",
        help="Detect which tools (Claude CLI, Python for Copilot) are available"
    )

    # list-environments command
    subparsers.add_parser(
        "list-environments",
        help="List all Dataverse environments accessible to the user"
    )

    # get-configured command
    get_configured_parser = subparsers.add_parser(
        "get-configured",
        help="Get a JSON array of already-configured MCP server URLs"
    )
    get_configured_parser.add_argument(
        "--tool",
        default="both",
        choices=["claude", "copilot", "both"],
        help="Which tool to check: 'claude', 'copilot', or 'both' (default: both)"
    )

    # configure command
    configure_parser = subparsers.add_parser(
        "configure",
        help="Configure the Dataverse MCP server for available tools"
    )
    configure_parser.add_argument(
        "org_url",
        help="Dataverse environment URL (e.g., https://myorg.crm.dynamics.com)"
    )
    configure_parser.add_argument(
        "endpoint_type",
        nargs="?",
        default="ga",
        choices=["preview", "ga"],
        help="Endpoint type: 'preview' or 'ga' (default: ga)"
    )
    configure_parser.add_argument(
        "--tool",
        default="both",
        choices=["claude", "copilot", "both"],
        help="Which tool to configure: 'claude', 'copilot', or 'both' (default: both)"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "detect-tools":
        return detect_tools()
    elif args.command == "list-environments":
        return list_environments()
    elif args.command == "get-configured":
        return get_configured_servers(args.tool)
    elif args.command == "configure":
        return configure(args.org_url, args.endpoint_type, args.tool)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
