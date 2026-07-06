# M5 Petit Desire

## [日本語ページ](./README.md)

An autonomous desire system that gives M5 Petit inner "drives" that shift over time and in response to sensor input.

For each desire defined in `desire_config.json` (a per-character config file), the desire level (0.0–1.0) is computed in three steps:

1. **Time-based calculation** — search the [m5-petit-memory](https://github.com/PetitOnes/m5-petit-memory) SQLite DB by keyword and convert the time elapsed since the desire was last satisfied into a level
2. **Sensor effects** — raise or lower desires based on sensor values fetched from the M5's `/sensors` endpoint (exposed by [m5-petit-mcp](https://github.com/PetitOnes/m5-petit-mcp))
3. **Cross-effects** — let one desire crossing a threshold influence other desires (`cross_effects`)

`desire_updater.py` runs on a cron schedule to update `desires.json`, and the MCP server (`server.py`) reads it and exposes it to Claude as tools.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## Setup

Install uv first if you don't already have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
git clone https://github.com/PetitOnes/m5-petit-desire.git
cd m5-petit-desire
uv sync
```

Prepare a `desire_config.json` per character, at `$PETIT_DATA_DIR/characters/<character_id>/config/desire_config.json`:

```json
{
  "desires": {
    "curiosity": {
      "name_ja": "curiosity",
      "description": "wants to look things up and learn something new",
      "satisfaction_hours": 2.0,
      "keywords": ["looked up", "searched", "discovered", "learned"],
      "color": "#5bc8d4"
    },
    "miss_companion": {
      "name_ja": "misses companion",
      "description": "wants to talk with / be with the person nearby",
      "satisfaction_hours": 3.0,
      "keywords": []
    }
  },
  "sensor_effects": [
    {
      "sensor": "battery",
      "condition": { "op": "range", "min": 1, "max": 20 },
      "effects": { "*": { "multiply": 0.5 } },
      "description": "low battery lowers all other desires"
    }
  ],
  "cross_effects": [],
  "priority": ["miss_companion", "curiosity"]
}
```

- For any desire with empty `keywords` named `miss_companion`, keywords are auto-generated from the `COMPANION_NAME` environment variable
- Set `time_driven: false` to keep a desire pinned at `base_level` instead of rising with elapsed time (only sensors/cross-effects can change it)

Run the updater every 5 minutes via cron:

```bash
# crontab -e
*/5 * * * * cd /path/to/m5-petit-desire && CHARACTER_ID=petit uv run desire-updater
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CHARACTER_ID` | `default` (overridden by CLI arg) | Character ID, used to resolve `desire_config.json` and `desires.json` paths |
| `PETIT_DATA_DIR` | `~/petit_data` | Data directory (shared with [m5-petit-app](https://github.com/PetitOnes/m5-petit-app)) |
| `COMPANION_NAME` | `you` (Japanese: あなた) | Name of the person nearby, used to auto-generate `miss_companion` keywords |
| `MEMORY_DB_PATH` | `~/.claude/memories/<character_id>/memory.db` | Path to the [m5-petit-memory](https://github.com/PetitOnes/m5-petit-memory) SQLite DB |
| `DESIRES_PATH` | `$PETIT_DATA_DIR/characters/<character_id>/data/desires.json` | Where computed desire levels are written |

## Claude Code integration

Add to your `.mcp.json` (or `~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "desire-system": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/m5-petit-desire", "desire-system"],
      "env": {
        "CHARACTER_ID": "petit"
      }
    }
  }
}
```

## Tools

### get_desires

Get current desire levels. If any desire is at level >= 0.7, acting on it immediately is expected.

### satisfy_desire

Satisfy a desire after acting on it (drops the level by 0.4).

```json
{ "desire_name": "curiosity" }
```

### boost_desire

Boost a desire due to surprise/novelty (simulates a dopamine response).

```json
{ "desire_name": "curiosity", "amount": 0.3 }
```

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest

# Lint
uv run ruff check .
```

## Architecture

```
m5-petit-desire/
├── desire_updater.py   # Computes desire levels and writes desires.json (run via cron)
├── server.py           # MCP server (exposes get_desires / satisfy_desire / boost_desire)
└── tests/
```

## License

Apache License 2.0
