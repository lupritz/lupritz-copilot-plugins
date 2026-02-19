#!/usr/bin/env python3
"""
mcp_setup.py — MCP server configuration tool for Dataverse and GitHub Copilot.

This script helps configure the Dataverse MCP server for GitHub Copilot.

Commands:
  list-environments    List all Dataverse environments accessible to the user
  get-configured       Get a JSON array of already-configured MCP server URLs
  configure            Configure the Dataverse MCP server for GitHub Copilot
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Set
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# Constants
SERVER_NAME_PREFIX = "DataverseMcp"


def get_copilot_config_path() -> tuple[Path, str]:
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


def extract_org_name(org_url: str) -> str:
    """
    Extract the organization identifier from a Dataverse URL.

    Args:
        org_url: The Dataverse environment URL (e.g., https://orgbc9a965c.crm10.dynamics.com)

    Returns:
        The organization identifier (e.g., orgbc9a965c)
    """
    # Remove protocol and trailing slash
    url = org_url.replace("https://", "").replace("http://", "").rstrip("/")

    # Extract the subdomain (org identifier)
    org_id = url.split(".")[0]

    return org_id


def get_server_name(org_url: str) -> str:
    """
    Generate a unique server name for the given Dataverse organization URL.

    Args:
        org_url: The Dataverse environment URL

    Returns:
        A unique server name (e.g., DataverseMcporgbc9a965c)
    """
    org_id = extract_org_name(org_url)
    return f"{SERVER_NAME_PREFIX}{org_id}"


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


def get_configured_servers() -> int:
    """
    Get a JSON array of already-configured MCP server URLs from Copilot config.

    Returns:
        Exit code (always 0)
    """
    urls: Set[str] = set()

    # Check mcp-config.json for Copilot
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


def configure_copilot(mcp_url: str, server_name: str) -> None:
    """
    Configure GitHub Copilot by updating mcp-config.json.

    Args:
        mcp_url: The MCP server URL to configure
        server_name: The unique name for this MCP server
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
    config[servers_key][server_name] = {
        "type": "http",
        "url": mcp_url,
    }

    # Write back
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def configure(org_url: str, endpoint_type: str = "ga") -> int:
    """
    Configure the Dataverse MCP server for GitHub Copilot.

    Args:
        org_url: Dataverse environment URL (e.g., https://myorg.crm.dynamics.com)
        endpoint_type: "preview" or "ga" (default: ga)

    Returns:
        Exit code (0 on success, 1 on error)
    """
    # Build MCP URL
    if endpoint_type == "preview":
        mcp_url = f"{org_url}/api/mcp_preview"
    else:
        mcp_url = f"{org_url}/api/mcp"

    # Generate unique server name from org URL
    server_name = get_server_name(org_url)

    config_file, display_path = get_copilot_config_path()

    # Configure Copilot
    try:
        configure_copilot(mcp_url, server_name)
    except Exception as e:
        print(f"Error: Failed to configure GitHub Copilot: {e}", file=sys.stderr)
        return 1

    # Print success message
    print("Dataverse MCP server registered successfully for GitHub Copilot.")
    print(f"  Server name    : {server_name}")
    print(f"  Server URL     : {mcp_url}")
    print(f"  Copilot config : {config_file}")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="MCP server configuration tool for Dataverse and GitHub Copilot",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # list-environments command
    subparsers.add_parser(
        "list-environments",
        help="List all Dataverse environments accessible to the user"
    )

    # get-configured command
    subparsers.add_parser(
        "get-configured",
        help="Get a JSON array of already-configured MCP server URLs from Copilot config"
    )

    # configure command
    configure_parser = subparsers.add_parser(
        "configure",
        help="Configure the Dataverse MCP server for GitHub Copilot"
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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "list-environments":
        return list_environments()
    elif args.command == "get-configured":
        return get_configured_servers()
    elif args.command == "configure":
        return configure(args.org_url, args.endpoint_type)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
