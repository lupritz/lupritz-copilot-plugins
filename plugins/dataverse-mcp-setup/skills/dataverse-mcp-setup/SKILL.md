---
description: Configure your Dataverse environment for the MCP server
---

# Setup Dataverse MCP

This skill configures the Dataverse MCP server with your organization's environment URL. If the user provided a URL it is: $ARGUMENTS.

## Instructions

### 0. Detect available tools and check already-configured MCP servers

First, detect which tools are available on the system:

```bash
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py detect-tools
```

This returns JSON indicating which tools are available:
```json
{
  "claude": true,
  "copilot": true
}
```

Then check already-configured MCP servers by running the `get-configured` command. You can optionally specify `--tool` to check only a specific tool (claude, copilot, or both):

```bash
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py get-configured --tool both
```

The script checks the specified tool(s) and outputs a JSON array of MCP server URLs that are already registered, for example:

```json
["https://orgfbb52bb7.crm.dynamics.com/api/mcp"]
```

Store this list as `CONFIGURED_URLS`. If the script fails for any reason, treat `CONFIGURED_URLS` as empty (`[]`) and continue — this step must never block the skill.

### 1. Fetch the list of Dataverse environments

Run the `mcp_setup.py` script with the `list-environments` command, located in the same directory as this skill file:

```bash
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py list-environments
```

The script uses the Azure CLI to authenticate and calls the Power Apps API to retrieve all environments where `databaseType` is `CommonDataService`. It outputs a JSON array to stdout:

```json
[
  { "displayName": "My Org (default)", "instanceUrl": "https://orgfbb52bb7.crm.dynamics.com" }
]
```

**If the script succeeds**, proceed to step 2.

**If the script fails** (Azure CLI not installed, Python not installed, user not logged in, network error, no environments found), tell the user what went wrong and fall back to step 1b.

### 1b. Fallback — ask for the URL manually

If step 1 failed, ask the user to provide their environment URL directly:

> Please enter your Dataverse environment URL.
>
> Example: `https://myorg.crm10.dynamics.com`
>
> You can find this in the Power Platform Admin Center under Environments.

Then skip to step 3.

### 2. Ask the user to select an environment

Present the environments as a numbered list. For each environment, check whether any URL in `CONFIGURED_URLS` starts with that environment's `instanceUrl` — if so, append **(already configured)** to the line.

> I found the following Dataverse environments on your account. Which one would you like to configure?
>
> 1. My Org (default) — `https://orgfbb52bb7.crm.dynamics.com` **(already configured)**
> 2. Another Env — `https://orgabc123.crm.dynamics.com`
>
> Enter the number of your choice, or type "manual" to enter a URL yourself.

If the user selects an already-configured environment, confirm that they want to re-register it (e.g. to change the endpoint type) before proceeding.

If the user types "manual", fall back to step 1b.

### 3. Confirm the selected URL

Take the `instanceUrl` from the chosen environment (or the manually entered URL) and strip any trailing slash. This is `USER_URL` for the remainder of the skill.

### 4. Confirm if the user wants "Preview" or "Generally Available (GA)" endpoint

If preview use `/api/mcp_preview`
If Generally Available (GA) use `/api/mcp`

When setting up the MCP configuration.

### 5. Determine which tool to configure

Based on the context (is this running in Claude or Copilot?) and user preference, determine which tool should be configured. The options are:
- `claude` - Configure only Claude Code CLI
- `copilot` - Configure only GitHub Copilot
- `both` - Configure both tools (if available)

### 6. Register the MCP server

Run the `mcp_setup.py` script with the `configure` command. Pass the validated environment URL, the chosen endpoint type (`preview` or `ga`), and the `--tool` parameter.

```bash
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py configure "USER_URL" "ENDPOINT_TYPE" --tool "TOOL_CHOICE"
```

For example:
```bash
# Configure only Claude CLI
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py configure "https://org.crm.dynamics.com" "ga" --tool claude

# Configure only Copilot
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py configure "https://org.crm.dynamics.com" "ga" --tool copilot

# Configure both (if both are available)
python3 /path/to/skills/dataverse-mcp-setup/mcp_setup.py configure "https://org.crm.dynamics.com" "ga" --tool both
```

The script will:
- **For Claude** — runs `claude mcp add --scope user` to register the server directly (if Claude CLI is installed)
- **For Copilot** — merges the `Dataverse` entry into `mcp-config.json` without overwriting other entries

Interpret the exit code as follows:

- **Exit 0 — success.** Proceed to step 7.
- **Any other non-zero exit code — error.** Report the error output to the user and proceed to step 8 (Troubleshooting).

### 7. Confirm success and instruct restart

Tell the user:

> ✅ Dataverse MCP server configured for `USER_URL`.
>
> **IMPORTANT: You must restart your session for the changes to take effect.**
>
> - If using Claude Code: Exit this session completely and start a new one
> - If using GitHub Copilot: Restart your editor or reload the window
>
> Once restarted, you will be able to:
> - List all tables in your Dataverse environment
> - Query records from any table
> - Create, update, or delete records
> - Explore your schema and relationships

### 8. Troubleshooting

If something goes wrong, help the user check:

- The URL format is correct (`https://<org>.<region>.dynamics.com`)
- They have access to the Dataverse environment
- The environment URL matches what's shown in the Power Platform Admin Center
- Their Environment Admin has enabled "Dataverse CLI MCP" in the Allowed Clients list
- Their Environment has Dataverse MCP enabled, and if they're trying to use the preview endpoint that is enabled.
- Their agent supports HTTP-based MCP servers with OAuth
