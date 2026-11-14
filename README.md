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

### rhythm — 朝に 0 へ戻り、光に合わせて進む欲求(任意)

「眠い」のような欲求を「満たした記憶からの時間」で動かすと、起きた直後でも上限に戻り、昼がいちばん眠くなります。欲求に `rhythm` を書くと、その欲求だけ次の形で動きます(書かなければ今までどおり)。

```json
"want_to_sleep": {
  "name_ja": "眠い",
  "satisfaction_hours": 8,
  "keywords": ["おやすみ", "眠った"],
  "rhythm": {"hours": 14, "dark_x": 2, "bright_x": 0.5, "relief_cap": 0.2, "relief_rate": 0.1}
}
```

- **0 に戻るのは朝だけ**。朝 = 夜の眠りのあと、暗い時間をまたいで、明るいところで最初に動き出したとき
- **昼の「おやすみ」は止まるだけ**。次に動き出したとき、休んだ長さに合わせて少し下がる(1 時間につき `relief_rate`、多くても `relief_cap`)
- **あとは光に合わせて進む**。ふつうの明るさなら `hours` 時間で上限(1.0)。暗くて動いていないときは `dark_x` 倍、明るいときは `bright_x` 倍の速さ。夜、暗いところで眠っているあいだは進まない
- 「おやすみ」を書かなかった夜(まっくらな時間が 4 時間続いた)と、記録が 6 時間より長く抜けた夜も、夜をまたいだと数える

| 項目 | 既定 | 意味 |
|---|---|---|
| `hours` | 12 | 朝から上限までの時間(ふつうの明るさのとき) |
| `dark_x` | 2 | 暗くて(`ambient` < 100)動いていないときの進み方 |
| `bright_x` | 0.5 | 明るい(`ambient` >= 300)ときの進み方 |
| `relief_cap` | 0.2 | 昼の休みで下がる割合の上限 |
| `relief_rate` | 0.1 | 昼の休み 1 時間あたりに下がる割合 |

使うもの:

- 明るさ・ジャイロ(センサー)。眠り始めは、`keywords` を含む記憶が書かれた時刻(夜の眠りは 20〜4 時に書かれたもの)
- あれば使うもの: 体の眠り・目覚めの記録(`characters/<id>/state/experience/YYYYMMDD.jsonl` の `sleep` / `woken`。10 分より短くまた眠った目覚めは数えない)、ぷちが動き出した記録(`.autonomous-logs/<id>/*_stream.jsonl` の始まり)
- 5 分ごとに 1 歩ずつ進むので、状態を `characters/<id>/data/desire_rhythm.json` に持つ。はじめての回は、これまでの計算の値から始まり、次の朝に 0 へ戻る
- センサーの効き方(電池など)と欲求どうしの効き方は、今までどおり、そのあとに掛かる

### relief — 体の出来事や言葉で、少しずつ満たされる欲求(任意)

「触られたい」のような欲求を「満たした言葉を含む記憶からの時間」で動かすと、言葉を書けば 0 に戻り、体が本当に触られたかは見ません。欲求に `relief` を書くと、その欲求だけ次の形で動きます(書かなければ今までどおり)。

```json
"want_touch": {
  "name_ja": "触られたい",
  "satisfaction_hours": 8,
  "keywords": ["なでてもらった", "触ってもらった"],
  "relief": {
    "hours": 48,
    "by": [
      {"event": "touch", "has": "strokes", "ratio": 0.6},
      {"event": "touch", "has": "taps", "ratio": 0.15, "cap": 0.4, "cap_hours": 6},
      {"event": "touch", "plain": true, "ratio": 0.3},
      {"event": "put_down", "min_carried_sec": 5, "ratio": 0.5},
      {"words": true, "ratio": 0.1, "cap": 0.3, "cap_hours": 12}
    ]
  }
}
```

- **時間で上がる**。`hours` 時間で上限(1.0)。書かなければ `satisfaction_hours`
- **起きたことのたびに、「いまの強さ × `ratio`」だけ下がる**。最初の 1 回がいちばん大きく効き、0 にはならない
- **`cap` を書いた決まりは、`cap_hours` のうちに下げられる割合が `cap` まで**。短いタッチを何回重ねても、撫でられた 1 回を超えない、という形にできる
- **5 分ごとの計算のたびに、前の計算から今までの記録を読む**。いつ触られても、次の計算で数える(記録が遅れて書かれる分、10 分さかのぼって読み、同じ出来事は二度数えない)

`by` に並べる決まり:

| 項目 | 意味 |
|---|---|
| `event` | 体の出来事の種類(experience-daemon の記録の `type`。`touch` / `put_down` など) |
| `words` | `true` なら、`keywords` を含む記憶が書かれたとき(`event` の代わりに書く) |
| `ratio` | 1 回で下がる割合(0 より大きく 1 より小さい) |
| `has` | 出来事の `detail` のこの値が 1 以上のときだけ(`touch` の `strokes` = 撫でられた / `taps` = 短く触られた)。1 件に何回入っていても、下がるのは 1 回分 |
| `plain` | `true` なら、`strokes` も `taps` も無い `touch` だけ(種類を返さない体のため) |
| `min_carried_sec` | `put_down` で、これより短く持たれた分は数えない(机の揺れを入れない) |
| `cap` / `cap_hours` | この決まりで、`cap_hours`(既定 6)時間のうちに下げられる割合の上限 |
| `where` | 出来事の中身がこの値のときだけ。`{"cam_to": "<世話人の ID>"}` で「体の設定がその人だったとき」 |
| `scale_by` / `full_at` / `scale_floor` | 出来事のこの値に合わせて効き方を変える。値が `full_at` 以上で `ratio` どおり、小さければその割合だけ。`scale_floor` は、値が小さい・無いときでも効く割合。「久しぶりに近づいたときほど大きく」に使う |
| `raise` | `ratio` の代わりに書くと、下げずに上げる決まりになる(足す量)。`cap` は、`cap_hours` のうちに足せる量の上限 |
| `delay_min` | 起きてからこれだけ間をあけて効かせる |

使うもの:

- 体の出来事の記録(`characters/<id>/state/experience/YYYYMMDD.jsonl`)。無ければ、言葉の決まりだけが効く
- あれば読むもの: その子と人との接点(`characters/<id>/state/contact/YYYYMMDD.jsonl`)、家の出来事(`state/presence/YYYYMMDD.jsonl`。人感センサーの `arrived` など)。形は体の出来事と同じ 1 行 1 件で、中身は `detail` か同じ段に書く
- 状態を `characters/<id>/data/desire_relief.json` に持つ。はじめての回は、これまでの計算の値から始まる
- `rhythm` も書いてある欲求は、`rhythm` で動く
- センサーの効き方と欲求どうしの効き方は、今までどおり、そのあとに掛かる

気配で動く欲求と、気配のあと少し遅れて上がる欲求の例:

```json
"miss_companion": {
  "relief": {"hours": 24, "by": [
    {"event": "arrived", "ratio": 0.2, "scale_by": "away_min", "full_at": 240, "scale_floor": 0.25, "cap": 0.2, "cap_hours": 12}
  ]}
},
"want_to_talk": {
  "relief": {"hours": 24, "by": [
    {"event": "talked", "ratio": 0.7},
    {"event": "arrived", "raise": 0.1, "delay_min": 15, "cap": 0.3, "cap_hours": 24}
  ]}
}
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

本プロジェクトは [lifemate-ai/embodied-claude](https://github.com/lifemate-ai/embodied-claude)（MITライセンス）の desire-system コンポーネントを元に、M5 Petit向けに大幅に改変したものです。元のライセンスと著作権表示は [NOTICE](NOTICE) を参照してください。
