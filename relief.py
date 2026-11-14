"""relief — 体の出来事や言葉で、少しずつ満たされる欲求(「触られたい」など)。

ふつうの欲求は「満たした言葉を含む記憶が最後に書かれてからの時間」で動く。
言葉を書けば 0 に戻り、体が本当に触られたかは見ない。
desire_config.json の欲求に "relief" を書くと、その欲求だけ次の形で動く:

- 時間で上がる。hours 時間で上限(1.0)
- 体の出来事(experience-daemon の記録)や、満たした言葉があるたびに、
  「いまの強さ × ratio」だけ下がる。最初の 1 回がいちばん大きく効き、0 にはならない
- ratio は by に並べた決まりごとに書く(撫でられた・短く触られた・持たれた・言葉)
- cap と cap_hours を書いた決まりは、cap_hours のうちに下げられる割合が cap まで。
  短いタッチを何回重ねても、撫でられた 1 回を超えない、という形にできる

5 分ごとの計算のたびに、前の計算から今までの記録を読む(いつ触られても、次の計算で数える)。
欲求ごとの状態を data/desire_relief.json に持つ。step() は状態を受け取って返すだけ。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

LOOKBACK = timedelta(
    minutes=10
)  # 記録は少し遅れて書かれる(タッチは 30 秒まとめる)。その分さかのぼって読む
WORDS = "words"  # 言葉(満たした言葉を含む記憶)を表す、出来事の種類


@dataclass(frozen=True)
class Happening:
    """起きたこと 1 件。kind は体の記録の type(touch / put_down …)か WORDS"""

    ts: datetime
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.ts.isoformat(timespec='seconds')}|{self.kind}"


@dataclass(frozen=True)
class Rule:
    """下がり方の決まり 1 つ(設定の relief.by の 1 件)"""

    kind: str  # 体の記録の type、または WORDS
    ratio: float  # 1 回で「いまの強さ」のうち下がる割合(0〜1)
    has: str | None = None  # detail のこの値が 1 以上のときだけ(touch の "strokes" / "taps")
    plain: bool = False  # detail に strokes も taps も無い touch だけ(種類を返さない体のため)
    min_carried_sec: float = 0.0  # put_down: これより短く持たれた分は数えない(机の揺れを入れない)
    cap: float | None = None  # cap_hours のうちに、この決まりで下げられる割合の上限
    cap_hours: float = 6.0

    @classmethod
    def from_dict(cls, raw: Any) -> Rule | None:
        if not isinstance(raw, dict):
            return None
        kind = WORDS if raw.get("words") else raw.get("event")
        ratio = _num(raw.get("ratio"))
        if not isinstance(kind, str) or not kind or ratio is None or not 0 < ratio < 1:
            return None
        cap = _num(raw.get("cap"))
        return cls(
            kind=kind,
            ratio=ratio,
            has=raw["has"] if isinstance(raw.get("has"), str) else None,
            plain=raw.get("plain") is True,
            min_carried_sec=_num(raw.get("min_carried_sec")) or 0.0,
            cap=cap if cap is not None and 0 < cap < 1 else None,
            cap_hours=_num(raw.get("cap_hours")) or 6.0,
        )

    def matches(self, h: Happening) -> bool:
        if h.kind != self.kind:
            return False
        d = h.detail
        if self.has is not None and not (_num(d.get(self.has)) or 0) >= 1:
            return False
        if self.plain and ((_num(d.get("strokes")) or 0) or (_num(d.get("taps")) or 0)):
            return False
        if self.min_carried_sec and (_num(d.get("carried_sec")) or 0) < self.min_carried_sec:
            return False
        return True


@dataclass(frozen=True)
class ReliefParams:
    hours: float
    rules: tuple[Rule, ...]

    @classmethod
    def from_dict(cls, raw: Any, default_hours: float) -> ReliefParams:
        """設定の "relief" から。読めない決まりは飛ばす"""
        raw = raw if isinstance(raw, dict) else {}
        hours = _num(raw.get("hours"))
        by = raw.get("by") if isinstance(raw.get("by"), list) else []
        rules = tuple(r for r in (Rule.from_dict(x) for x in by) if r is not None)
        return cls(hours=hours if hours and hours > 0 else default_hours, rules=rules)

    @property
    def kinds(self) -> set[str]:
        return {r.kind for r in self.rules}


@dataclass
class ReliefState:
    level: float = 1.0
    ts: str | None = None  # 最後に進めた時刻
    done: list[str] = field(
        default_factory=list
    )  # もう数えた出来事(さかのぼって読むので、二度数えない)
    # 上限つきの決まりが、最近どれだけ下げたか: {決まりの番号: [[時刻, 掛けた値], …]}
    applied: dict[str, list[list[Any]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any) -> ReliefState:
        if not isinstance(raw, dict):
            return cls()
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        try:
            return cls(**known)
        except TypeError:
            return cls()


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _dt(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value[:19]) if isinstance(value, str) and value else None
    except ValueError:
        return None


def _factor(rule: Rule, index: int, state: ReliefState, when: datetime) -> float:
    """この決まりで、いま掛ける値(1 - ratio)。上限つきなら、窓のうちの残りまで"""
    factor = 1.0 - rule.ratio
    if rule.cap is None:
        return factor
    window = timedelta(hours=rule.cap_hours)
    recent = [
        (t, f)
        for t, f in (
            (_dt(x[0]), _num(x[1])) for x in state.applied.get(str(index), []) if len(x) == 2
        )
        if t is not None and f is not None and when - t < window
    ]
    used = 1.0
    for _, f in recent:
        used *= f
    floor = 1.0 - rule.cap  # 窓のうちに掛けてよい値の下限
    factor = min(1.0, max(factor, floor / used)) if used > 0 else 1.0
    kept = [[t.isoformat(timespec="seconds"), f] for t, f in recent]
    if factor < 1.0:
        kept.append([when.isoformat(timespec="seconds"), factor])
    state.applied[str(index)] = kept
    return factor


def step(
    state: ReliefState, params: ReliefParams, now: datetime, happenings: list[Happening]
) -> ReliefState:
    """1 歩進める: 起きたことを時刻の順に見て、そのたびに下げ、あいだは時間で上げる"""
    prev = _dt(state.ts) or now
    level = min(1.0, max(0.0, state.level))
    done = set(state.done)
    new = ReliefState(level=level, ts=state.ts, done=list(state.done), applied=dict(state.applied))
    clock = prev
    for h in sorted(happenings, key=lambda x: x.ts):
        if h.key in done or h.ts > now:
            continue
        done.add(h.key)
        when = max(h.ts, clock)  # 遅れて届いた分は、いまの時計で数える(時計を戻さない)
        level = min(1.0, level + (when - clock).total_seconds() / 3600 / params.hours)
        clock = when
        for index, rule in enumerate(params.rules):
            if rule.matches(h):
                level *= _factor(rule, index, new, when)
    level = min(1.0, level + max(0.0, (now - clock).total_seconds()) / 3600 / params.hours)
    keep_from = now - LOOKBACK - timedelta(minutes=5)
    new.level = round(level, 4)
    new.ts = now.isoformat(timespec="seconds")
    new.done = sorted(k for k in done if (_dt(k.split("|")[0]) or now) >= keep_from)
    return new


# ---------------------------------------------------------------------------
# 起きたことを集める(家にあるものだけ。無ければ空)
# ---------------------------------------------------------------------------


def body_happenings(
    experience_dir: Path, kinds: set[str], since: datetime, until: datetime
) -> list[Happening]:
    """体の出来事(experience-daemon の YYYYMMDD.jsonl)のうち、kinds に入る種類"""
    out: list[Happening] = []
    day = since.date()
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
                when = _dt(ev.get("ts"))
                kind = ev.get("type")
            except (json.JSONDecodeError, AttributeError):
                continue
            if when is None or kind not in kinds or not (since < when <= until):
                continue
            detail = ev.get("detail")
            out.append(Happening(when, kind, detail if isinstance(detail, dict) else {}))
        day += timedelta(days=1)
    return out


def word_happenings(
    db_path: Path, keywords: list[str], since: datetime, until: datetime
) -> list[Happening]:
    """言葉: 満たした言葉(keywords)を含む記憶が書かれた時刻"""
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
        when = _dt(ts)
        if when:
            out.append(Happening(when, WORDS))
    return out


def gather(
    params: ReliefParams,
    experience_dir: Path,
    db_path: Path,
    keywords: list[str],
    since: datetime,
    until: datetime,
) -> list[Happening]:
    """since から LOOKBACK さかのぼったところ〜until に起きたこと。二度数えないのは step() の役目"""
    start = since - LOOKBACK
    out = body_happenings(experience_dir, params.kinds - {WORDS}, start, until)
    if WORDS in params.kinds:
        out += word_happenings(db_path, keywords, start, until)
    return out


# ---------------------------------------------------------------------------
# 状態の読み書き
# ---------------------------------------------------------------------------


def load_states(path: Path) -> dict[str, ReliefState]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: ReliefState.from_dict(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def save_states(path: Path, states: dict[str, ReliefState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({k: asdict(v) for k, v in states.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
