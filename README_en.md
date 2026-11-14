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

### rhythm — a desire that resets in the morning and follows the light (optional)

Driving something like sleepiness by "time since the last satisfying memory" makes it return to the ceiling right after waking and peak at noon. Add `rhythm` to a desire and only that desire follows this shape instead (omit it to keep the default behaviour):

```json
"want_to_sleep": {
  "name_ja": "眠い",
  "satisfaction_hours": 8,
  "keywords": ["おやすみ", "眠った"],
  "rhythm": {"hours": 14, "dark_x": 2, "bright_x": 0.5, "relief_cap": 0.2, "relief_rate": 0.1}
}
```

- **It resets to 0 only in the morning**: after a night's sleep that spanned dark hours, at the first activity in the light
- **A daytime "good night" is only a rest**: at the next activity the level drops a little, by rest length (`relief_rate` per hour, at most `relief_cap`)
- **Otherwise it follows the light**: `hours` to reach 1.0 in ordinary light, `dark_x` times faster when dark and still, `bright_x` times when bright. It does not advance while asleep in the dark at night
- A night without a "good night" memory (4 hours of pitch dark) and a gap of more than 6 hours in the updates also count as a night

Inputs: ambient light and gyro from the sensors; sleep starts from memories containing the `keywords`; optionally the body's `sleep` / `woken` events (`characters/<id>/state/experience/`) and session starts (`.autonomous-logs/<id>/*_stream.jsonl`). State is kept in `characters/<id>/data/desire_rhythm.json`. Sensor effects and cross effects still apply afterwards. See the Japanese README for the parameter table.

### relief — a desire eased little by little by body events and words (optional)

Add `relief` to a desire (e.g. "wants to be touched") and it rises with time (`hours` to the ceiling) and drops by `level × ratio` each time something listed in `by` happens: a body event recorded by experience-daemon (`{"event": "touch", "has": "strokes", "ratio": 0.6}`, `{"event": "put_down", "min_carried_sec": 5, "ratio": 0.5}`) or a memory containing the `keywords` (`{"words": true, "ratio": 0.1}`). The first one counts most and the level never reaches zero. A rule with `cap` / `cap_hours` can lower the level by at most `cap` within that window, so many short taps never beat one stroke. A rule can also match on the event's contents (`where`), scale with a value in it (`scale_by` / `full_at`, e.g. "the longer away, the more an arrival counts"), or raise the level instead of lowering it (`raise`, optionally after `delay_min`). Besides body events it reads `characters/<id>/state/contact/` and the house's `state/presence/`. State is kept in `characters/<id>/data/desire_relief.json`. See the Japanese README for the full table.

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

This project is derived from the desire-system component of [lifemate-ai/embodied-claude](https://github.com/lifemate-ai/embodied-claude) (MIT License), substantially modified for M5 Petit. See [NOTICE](NOTICE) for the original license and copyright notice.
