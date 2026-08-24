# Create an MCP stdio tool

Thyca is an MCP **client**. New capabilities are child processes, not builtins.

1. Write a FastMCP script in the workspace (not under `~/.thyca`):

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

@mcp.tool()
def forecast(city: str) -> str:
    return "..."

if __name__ == "__main__":
    mcp.run()
```

2. `write`/`edit` `~/.thyca/config.json` — add one entry. Keys go in `env`:

```json
"mcpServers": {
  "weather": {
    "command": "python3",
    "args": ["/absolute/path/to/weather.py"],
    "env": { "OPENWEATHER_API_KEY": "..." }
  }
}
```

Server name: `[A-Za-z0-9_-]+` only.

3. Ask the user to restart `thyca` / `thyca --serve`. No hot reload. After restart the model sees `weather__forecast`.

Do not put secrets in the script. Child stderr goes to the terminal, not into tool results. Tools only — no resources/HTTP transport.
