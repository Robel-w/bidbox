# Borrowed MCP components

## Official Filesystem MCP Server

- **Name:** `@modelcontextprotocol/server-filesystem` from `modelcontextprotocol/servers`
- **License:** MIT
- **Version pin:** `2025.1.14` (override with `BIDBOX_FILESYSTEM_MCP_VERSION`)
- **Command:** `npx -y @modelcontextprotocol/server-filesystem@2025.1.14 data/fixtures`
- **Purpose:** Re-verifying citations against the local synthetic OCDS fixture is generic sandboxed file access; the maintained official server provides the allow-list and path handling we should not rewrite.

Re-verifying citations against the live portal is generic HTTP+markdown fetching; the maintained official server does it with robots/size handling we'd otherwise rewrite and get wrong.

The graph passes only `data/fixtures` as an allowed directory and uses the server's read tool (`read_text_file` or the pinned package's `read_file`) for source verification. Offline or unavailable borrowed-server calls are recorded as limitations; they are never treated as verified evidence.
