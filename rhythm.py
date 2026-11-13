"""rhythm — 朝に 0 へ戻り、光に合わせて進む欲求(「眠い」など)。

ふつうの欲求は「満たした言葉を含む記憶が最後に書かれてからの時間」で上がる。
眠さをそれで動かすと、起きた直後でも上限に戻り、昼がいちばん眠くなる。
desire_config.json の欲求に "rhythm" を書くと、その欲求だけ次の形で動く:

- 0 に戻るのは朝だけ。朝 = 夜の眠りのあと、暗い時間をまたいで、明るいところで最初に動き出したとき
- 昼の「おやすみ」は止まるだけ。次に動き出したとき、休んだ長さに合わせて少し下がる
- あとは光に合わせて進む。ふつうの明るさなら hours 時間で上限(1.0)。
  暗くて動かないときは dark_x 倍、明るいときは bright_x 倍の速さ。
  夜、暗いところで眠っているあいだは進まない
- 「おやすみ」を書かなかった夜(まっくらな時間が続いた)・記録が抜けた夜も、夜をまたいだと数える

5 分ごとに 1 歩ずつ進むので、欲求ごとの状態を data/desire_rhythm.json に持つ。
step() は状態を受け取って返すだけ。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DARK = 100  # 明るさ(ambient)がこれより下なら「暗い」
BRIGHT = 300  # これ以上なら「明るい」
STILL = 3.0  # ジャイロの大きさがこれより下なら「動いていない」
NIGHT_FROM, NIGHT_TO = 20, 4  # この間に書いた「おやすみ」を、夜の眠りの始まりと数える
NIGHT_DARK_H = (
    3  # 夜の「おやすみ」のあと、暗い時間がこれだけあれば、次に明るいところで動き出したときが朝
)
NIGHT_ANY_H = 6  # 夜の「おやすみ」からこれだけたっていれば、暗い時間が数えられなくても朝
PITCH_DARK = 20  # 「おやすみ」を書かなかった夜のため: 明るさがこれより下の、まっくらな時間が
PITCH_DARK_H = 4  #   これだけ続いたら、夜の眠りと数える(曇りの昼の暗さでは数えない)
GAP_H = 0.5  # 前の 1 歩からこれより空いたら、記録が抜けたと見る(その間の明るさは分からない)
GAP_NIGHT_H = 6  # これだけ抜けたら、夜をまたいだと数える
BRIEF_WAKE_MIN = 10  # 体が起きても、これより短くまた眠ったら、起きたと数えない

Event = tuple[datetime, str]  # (時刻, "sleep" = 眠り始め | "move" = 動き出した)


@dataclass(frozen=True)
class RhythmParams:
    hours: float = 12.0  # 朝から上限(1.0)までの時間(ふつうの明るさのとき)
    dark_x: float = 2.0  # 暗くて動いていないときの進み方(倍)
    bright_x: float = 0.5  # 明るいときの進み方(倍)
    relief_cap: float = 0.2  # 昼の休みで下がる割合の上限
    relief_rate: float = 0.1  # 昼の休み 1 時間あたりに下がる割合

    @classmethod
    def from_dict(cls, raw: Any) -> RhythmParams:
        """設定の "rhythm" から。正の数だけ受け取り、ほかは既定のまま"""
        values = {}
        for key in cls.__dataclass_fields__:
            v = raw.get(key) if isinstance(raw, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                values[key] = float(v)
        return cls(**values)


@dataclass
class RhythmState:
    level: float = 0.0
    ts: str | None = None  # 最後に進めた時刻
    rest_since: str | None = None  # 休み(眠り)に入った時刻。動き出すまで
    night_since: str | None = None  # 夜の眠りの始まり。朝が来るまで
    night_dark_h: float = 0.0  # 夜の眠りのあいだの、暗かった時間
    pitch_dark_h: float = 0.0  # まっくらな時間が続いている長さ

    @classmethod
    def from_dict(cls, raw: Any) -> RhythmState:
        if not isinstance(raw, dict):
            return cls()
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        try:
            return cls(**known)
        except TypeError:
            return cls()


def _dt(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def step(
    state: RhythmState,
    params: RhythmParams,
    now: datetime,
    sensor: dict[str, Any],
    events: list[Event],
) -> RhythmState:
    """1 歩進める。events は、前の 1 歩のあとから now までに起きたこと(眠り始め・動き出し)"""
    prev = _dt(state.ts)
    rest_since, night_since = _dt(state.rest_since), _dt(state.night_since)
    level, night_dark, pitch_dark = state.level, state.night_dark_h, state.pitch_dark_h

    ambient = sensor.get("ambient")
    known = isinstance(ambient, (int, float)) and not isinstance(ambient, bool)
    gyro = max((abs(sensor.get(k) or 0) for k in ("gx", "gy", "gz")), default=0)
    dark = known and ambient < DARK
    bright = known and ambient >= BRIGHT
    step_h = max(0.0, (now - prev).total_seconds() / 3600) if prev else 0.0
    gap = step_h > GAP_H

    pitch_dark = pitch_dark + step_h if known and ambient < PITCH_DARK and not gap else 0.0
    if night_since is None and (pitch_dark >= PITCH_DARK_H or step_h >= GAP_NIGHT_H):
        length = max(pitch_dark, step_h)
        night_since, night_dark = now - timedelta(hours=length), length
        rest_since = rest_since or night_since

    moved = False
    for when, kind in sorted(events):
        if kind == "move":
            moved = True
            continue
        if rest_since is None:
            rest_since = when
        if night_since is None and (when.hour >= NIGHT_FROM or when.hour < NIGHT_TO):
            night_since, night_dark = when, 0.0

    if night_since is not None and (dark or gap):
        night_dark += step_h
    in_night_sleep = night_since is not None and rest_since is not None and (dark or gap)
    if not in_night_sleep:
        speed = 1.0
        if known and not gap:
            speed = params.dark_x if dark and gyro < STILL else params.bright_x if bright else 1.0
        level = min(1.0, level + speed * step_h / params.hours)

    if moved or (
        step_h >= GAP_NIGHT_H and not dark
    ):  # 長く抜けたあとは、動き出しの記録も抜けている
        long_night = night_since is not None and (
            night_dark >= NIGHT_DARK_H or (now - night_since).total_seconds() >= NIGHT_ANY_H * 3600
        )
        if long_night and not dark:  # 朝
            level, night_since, night_dark, rest_since = 0.0, None, 0.0, None
        elif not dark:  # 昼の休みから動き出した(暗いまま動いただけなら、まだ休みの続き)
            if rest_since is not None:
                rest_h = (now - rest_since).total_seconds() / 3600
                level *= 1 - min(params.relief_cap, params.relief_rate * rest_h)
            rest_since = None

    return RhythmState(
        level=round(max(0.0, min(1.0, level)), 4),
        ts=now.isoformat(),
        rest_since=rest_since.isoformat() if rest_since else None,
        night_since=night_since.isoformat() if night_since else None,
        night_dark_h=round(night_dark, 4),
        pitch_dark_h=round(pitch_dark, 4),
    )


# ---------------------------------------------------------------------------
# 起きたことを集める(家にあるものだけ。無ければ空)
# ---------------------------------------------------------------------------


def sleep_marks(
    db_path: Path, keywords: list[str], since: datetime, until: datetime
) -> list[Event]:
    """眠り始め: 満たした言葉(keywords)を含む記憶が書かれた時刻"""
    if not keywords or not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cond = " OR ".join("content LIKE ?" for _ in keywords)
        rows = conn.execute(
            f"SELECT timestamp FROM memories WHERE timestamp > ? AND timestamp <= ? AND ({cond})",
            [since.isoformat(), until.isoformat(), *[f"%{k}%" for k in keywords]],
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    out = []
    for (ts,) in rows:
        when = _dt(ts[:19] if ts else None)
        if when:
            out.append((when, "sleep"))
    return out


def body_events(experience_dir: Path, since: datetime, until: datetime) -> list[Event]:
    """体の眠り・目覚め(experience-daemon の YYYYMMDD.jsonl にある sleep / woken)。

    目覚めは、BRIEF_WAKE_MIN のうちにまた眠っていないことを確かめてから数える。
    その分だけ遅れて返す
    """
    brief = timedelta(minutes=BRIEF_WAKE_MIN)
    raw: list[Event] = []
    day = (since - brief).date()
    while day <= until.date():
        try:
            lines = (
                (experience_dir / f"{day.strftime('%Y%m%d')}.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            )
        except OSError:
            lines = []
        for line in lines:
            try:
                ev = json.loads(line)
                if ev.get("type") in ("sleep", "woken"):
                    raw.append((datetime.fromisoformat(ev["ts"][:19]), ev["type"]))
            except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError):
                continue
        day += timedelta(days=1)
    out: list[Event] = [(t, "sleep") for t, k in raw if k == "sleep" and since < t <= until]
    for t, k in raw:
        if k != "woken" or not (since - brief < t <= until - brief):
            continue
        if any(k2 == "sleep" and timedelta(0) <= t2 - t <= brief for t2, k2 in raw):
            continue
        out.append((t, "move"))
    return out


def session_starts(log_dir: Path, since: datetime, until: datetime) -> list[Event]:
    """ぷちが動き出した時刻: 自律行動・会話の記録(YYYYMMDD_HHMMSS…_stream.jsonl)の始まり"""
    try:
        names = [f.name for f in log_dir.iterdir() if f.name.endswith("_stream.jsonl")]
    except OSError:
        return []
    out = []
    for name in names:
        try:
            when = datetime.strptime(name[:15], "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        if since < when <= until:
            out.append((when, "move"))
    return out


# ---------------------------------------------------------------------------
# 状態の読み書き
# ---------------------------------------------------------------------------


def load_states(path: Path) -> dict[str, RhythmState]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: RhythmState.from_dict(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def save_states(path: Path, states: dict[str, RhythmState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({k: asdict(v) for k, v in states.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
