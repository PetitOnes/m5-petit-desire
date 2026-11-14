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

- where で、出来事の中身が合うときだけ(「設定がこの人だった」など)
- scale_by で、値に合わせて効き方を変える(久しぶりに近づいたときほど大きく、など)
- ratio の代わりに raise を書いた決まりは、下げずに上げる。delay_min で、少し間をあけてから
  (気配のあと、しばらくして「話したい」がふっと上がる、など)

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

    kind: str  # 出来事の type、または WORDS
    ratio: float = 0.0  # 1 回で「いまの強さ」のうち下がる割合(0〜1)。raise_by と、どちらか片方
    raise_by: float = 0.0  # 1 回で上がる量(0〜1)。書くと、下げずに上げる決まりになる
    delay_min: float = 0.0  # 起きてからこれだけ間をあけて効かせる
    where: tuple[tuple[str, str], ...] = ()  # 出来事の中身がこの値のときだけ((項目, 値) の並び)
    scale_by: str | None = None  # 出来事のこの値(数)に合わせて効き方を変える
    full_at: float = 0.0  # scale_by の値がこれ以上で、ratio / raise_by どおりに効く
    scale_floor: float = 0.0  # 値が小さい・無いときでも、これだけは効く(0〜1)
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
        ratio, raise_by = _num(raw.get("ratio")), _num(raw.get("raise"))
        if not isinstance(kind, str) or not kind:
            return None
        if raise_by is not None:
            if not 0 < raise_by < 1:
                return None
            ratio = 0.0
        elif ratio is None or not 0 < ratio < 1:
            return None
        cap = _num(raw.get("cap"))
        where = raw.get("where") if isinstance(raw.get("where"), dict) else {}
        full_at = _num(raw.get("full_at")) or 0.0
        scale_by = raw.get("scale_by") if isinstance(raw.get("scale_by"), str) else None
        return cls(
            kind=kind,
            ratio=ratio,
            raise_by=raise_by or 0.0,
            delay_min=max(0.0, _num(raw.get("delay_min")) or 0.0),
            where=tuple(sorted((str(k), str(v)) for k, v in where.items())),
            scale_by=scale_by if scale_by and full_at > 0 else None,
            full_at=full_at,
            scale_floor=min(1.0, max(0.0, _num(raw.get("scale_floor")) or 0.0)),
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
        if any(str(d.get(k)) != v for k, v in self.where):
            return False
        return True

    def scale(self, h: Happening) -> float:
        """この出来事では、決まりの何割が効くか(scale_by が無ければ 1)"""
        if self.scale_by is None:
            return 1.0
        value = _num(h.detail.get(self.scale_by))
        share = min(1.0, value / self.full_at) if value is not None and value > 0 else 0.0
        return max(self.scale_floor, share)


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

    @property
    def lookback(self) -> timedelta:
        """どれだけさかのぼって読むか。間をあけて効かせる決まりがあれば、その分だけ長く"""
        delay = max((r.delay_min for r in self.rules), default=0.0)
        return LOOKBACK + timedelta(minutes=delay)


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


def _recent(rule: Rule, index: int, state: ReliefState, when: datetime) -> list[tuple]:
    """この決まりが、上限の窓(cap_hours)のうちに効かせた分 [(時刻, 値), …]"""
    window = timedelta(hours=rule.cap_hours)
    return [
        (t, f)
        for t, f in (
            (_dt(x[0]), _num(x[1])) for x in state.applied.get(str(index), []) if len(x) == 2
        )
        if t is not None and f is not None and when - t < window
    ]


def _remember(index: int, state: ReliefState, recent: list[tuple], when: datetime, value: float):
    kept = [[t.isoformat(timespec="seconds"), f] for t, f in recent]
    kept.append([when.isoformat(timespec="seconds"), value])
    state.applied[str(index)] = kept


def _factor(rule: Rule, index: int, state: ReliefState, when: datetime, share: float) -> float:
    """下げる決まりで、いま掛ける値(1 - ratio × share)。上限つきなら、窓のうちの残りまで"""
    factor = 1.0 - rule.ratio * share
    if rule.cap is None:
        return factor
    recent = _recent(rule, index, state, when)
    used = 1.0
    for _, f in recent:
        used *= f
    floor = 1.0 - rule.cap  # 窓のうちに掛けてよい値の下限
    factor = min(1.0, max(factor, floor / used)) if used > 0 else 1.0
    if factor < 1.0:
        _remember(index, state, recent, when, factor)
    return factor


def _lift(rule: Rule, index: int, state: ReliefState, when: datetime, share: float) -> float:
    """上げる決まりで、いま足す量(raise_by × share)。上限つきなら、窓のうちに足せる残りまで"""
    amount = rule.raise_by * share
    if rule.cap is not None:
        recent = _recent(rule, index, state, when)
        amount = max(0.0, min(amount, rule.cap - sum(f for _, f in recent)))
        if amount > 0:
            _remember(index, state, recent, when, amount)
    return amount


def step(
    state: ReliefState, params: ReliefParams, now: datetime, happenings: list[Happening]
) -> ReliefState:
    """1 歩進める: 起きたことを効かせる時刻の順に見て、そのたびに下げ(上げ)、あいだは時間で上げる"""
    prev = _dt(state.ts) or now
    level = min(1.0, max(0.0, state.level))
    done = set(state.done)
    new = ReliefState(level=level, ts=state.ts, done=list(state.done), applied=dict(state.applied))
    due = []  # (効かせる時刻, 出来事, 決まりの番号, 決まり)
    for h in happenings:
        if h.key in done:  # 前の版の覚え方(出来事ごと)。その出来事は、もう全部数えた
            continue
        for index, rule in enumerate(params.rules):
            key = f"{h.key}|{index}"
            at = h.ts + timedelta(minutes=rule.delay_min)
            if key in done or at > now or not rule.matches(h):
                continue  # at > now は、まだ間をあけているところ。次の回に見る
            due.append((at, h, index, rule, key))
    clock = prev
    for at, h, index, rule, key in sorted(due, key=lambda x: x[0]):
        done.add(key)
        when = max(at, clock)  # 遅れて届いた分は、いまの時計で数える(時計を戻さない)
        level = min(1.0, level + (when - clock).total_seconds() / 3600 / params.hours)
        clock = when
        share = rule.scale(h)
        if rule.raise_by:
            level = min(1.0, level + _lift(rule, index, new, when, share))
        else:
            level *= _factor(rule, index, new, when, share)
    level = min(1.0, level + max(0.0, (now - clock).total_seconds()) / 3600 / params.hours)
    keep_from = now - params.lookback - timedelta(minutes=5)
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
    """出来事の記録(YYYYMMDD.jsonl)のうち、kinds に入る種類。

    体の出来事(experience-daemon)は中身を detail に持つ。家の出来事(気配など)は、
    中身を同じ段に持つ(place / away_min など)。どちらも detail として読む
    """
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
            detail = {k: v for k, v in ev.items() if k not in ("ts", "type", "detail")}
            if isinstance(ev.get("detail"), dict):
                detail.update(ev["detail"])
            out.append(Happening(when, kind, detail))
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
    event_dirs: Path | list[Path],
    db_path: Path,
    keywords: list[str],
    since: datetime,
    until: datetime,
) -> list[Happening]:
    """since から params.lookback さかのぼったところ〜until に起きたこと。

    event_dirs は、出来事の記録の置き場所(体の出来事、家の出来事 …)。二度数えないのは step() の役目
    """
    dirs = [event_dirs] if isinstance(event_dirs, Path) else list(event_dirs)
    start = since - params.lookback
    out: list[Happening] = []
    for d in dirs:
        out += body_happenings(d, params.kinds - {WORDS}, start, until)
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
