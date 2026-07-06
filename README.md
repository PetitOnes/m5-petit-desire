# M5 Petit Desire

## [EnglishPage](./README_en.md)

M5 Petitに、時間経過とセンサー入力に基づいて変化する内的な「欲求」を持たせる自律欲求システムです。

`desire_config.json`(キャラクターごとの設定ファイル)で定義した欲求それぞれについて、3段階で欲求レベル(0.0〜1.0)を計算します。

1. **時間ベース計算** — [m5-petit-memory](https://github.com/PetitOnes/m5-petit-memory)のSQLite DBをキーワード検索し、最後にその欲求が満たされてからの経過時間を欲求レベルに変換
2. **センサー効果の適用** — M5の`/sensors`エンドポイント([m5-petit-mcp](https://github.com/PetitOnes/m5-petit-mcp)が公開)から取得したセンサー値で欲求を増減
3. **欲求間の相互作用** — ある欲求が閾値を超えたときに他の欲求へ影響を与える(`cross_effects`)

`desire_updater.py`をcronで定期実行して`desires.json`を更新し、MCPサーバー(`server.py`)がそれを読んでClaudeにツールとして提供します。

## 必要環境

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## セットアップ

uvが未インストールの場合は先にインストールします。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
git clone https://github.com/PetitOnes/m5-petit-desire.git
cd m5-petit-desire
uv sync
```

`desire_config.json`をキャラクターごとに用意します(`$PETIT_DATA_DIR/characters/<character_id>/config/desire_config.json`)。

```json
{
  "desires": {
    "curiosity": {
      "name_ja": "知りたい",
      "description": "気になることを調べたい、新しいことを知りたい好奇心",
      "satisfaction_hours": 2.0,
      "keywords": ["調べた", "検索した", "発見した", "学んだ"],
      "color": "#5bc8d4"
    },
    "miss_companion": {
      "name_ja": "会いたい",
      "description": "一緒にいる人と話したい、一緒にいたい気持ち",
      "satisfaction_hours": 3.0,
      "keywords": []
    }
  },
  "sensor_effects": [
    {
      "sensor": "battery",
      "condition": { "op": "range", "min": 1, "max": 20 },
      "effects": { "*": { "multiply": 0.5 } },
      "description": "電池が減ると他の欲求が下がる"
    }
  ],
  "cross_effects": [],
  "priority": ["miss_companion", "curiosity"]
}
```

- `keywords`が空の欲求のうち`miss_companion`は、`COMPANION_NAME`環境変数から自動でキーワードを生成します(「〇〇と話した」「〇〇に伝えた」など)
- `time_driven: false`を指定すると時間経過では変化せず、`base_level`に留まります(センサー・相互作用のみで変化)

cronで5分ごとに更新します。

```bash
# crontab -e
*/5 * * * * cd /path/to/m5-petit-desire && CHARACTER_ID=petit uv run desire-updater
```

## 環境変数

| 変数名 | デフォルト | 説明 |
|----------|---------|-------------|
| `CHARACTER_ID` | `default`(コマンドライン引数が優先) | キャラクターID。`desire_config.json`や`desires.json`のパス決定に使う |
| `PETIT_DATA_DIR` | `~/petit_data` | データディレクトリ([m5-petit-app](https://github.com/PetitOnes/m5-petit-app)と共有) |
| `COMPANION_NAME` | `あなた` | 一緒にいる人の名前(`miss_companion`欲求のキーワード自動生成に使用) |
| `MEMORY_DB_PATH` | `~/.claude/memories/<character_id>/memory.db` | [m5-petit-memory](https://github.com/PetitOnes/m5-petit-memory)が使うSQLite DBのパス |
| `DESIRES_PATH` | `$PETIT_DATA_DIR/characters/<character_id>/data/desires.json` | 欲求レベルの出力先 |

## Claude Code連携

`.mcp.json`(または`~/.claude/settings.json`)に追加します。

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

## ツール一覧

### get_desires

現在の欲求レベルを取得します。レベルが0.7以上の欲求があれば、すぐに行動することが期待されます。

### satisfy_desire

行動した後に欲求を満たします(レベルが0.4下がる)。

```json
{ "desire_name": "curiosity" }
```

### boost_desire

驚き・新規性による欲求のブースト(ドーパミン応答のシミュレーション)。

```json
{ "desire_name": "curiosity", "amount": 0.3 }
```

## 開発

```bash
# 開発依存をインストール
uv sync --all-extras

# テスト実行
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest

# lint
uv run ruff check .
```

## アーキテクチャ

```
m5-petit-desire/
├── desire_updater.py   # 欲求レベルの計算・desires.jsonへの保存(cronから実行)
├── server.py           # MCPサーバー(get_desires/satisfy_desire/boost_desireを提供)
└── tests/
```

## License

Apache License 2.0
