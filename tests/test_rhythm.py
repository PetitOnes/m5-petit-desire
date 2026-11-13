"""rhythm(朝に 0 へ戻り、光に合わせて進む欲求)のテスト"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import rhythm
from rhythm import RhythmParams, RhythmState, step

P = RhythmParams(hours=12, dark_x=2, bright_x=0.5, relief_cap=0.2, relief_rate=0.1)
NORMAL = {"ambient": 200, "gx": 0, "gy": 0, "gz": 0}
BRIGHT = {"ambient": 1000, "gx": 0, "gy": 0, "gz": 0}
DARK = {"ambient": 50, "gx": 0, "gy": 0, "gz": 0}
PITCH = {"ambient": 3, "gx": 0, "gy": 0, "gz": 0}


def run(
    state: RhythmState, start: datetime, hours: float, sensor: dict, events_at: dict | None = None
) -> RhythmState:
    """5 分ごとに hours 時間ぶん進める。events_at は {時刻: 種類}"""
    events_at = events_at or {}
    t = start
    for _ in range(round(hours * 12)):
        prev, t = t, t + timedelta(minutes=5)
        events = [(when, kind) for when, kind in events_at.items() if prev < when <= t]
        state = step(state, P, t, sensor, events)
    return state


def fresh(at: datetime, level: float = 0.0) -> RhythmState:
    return RhythmState(level=level, ts=at.isoformat())


def test_rises_with_time_and_light():
    """ふつうの明るさで hours 時間かけて上限へ。暗くて動かないと速く、明るいとゆっくり"""
    t0 = datetime(2026, 11, 10, 8, 0)
    assert abs(run(fresh(t0), t0, 6, NORMAL).level - 0.5) < 0.01
    assert abs(run(fresh(t0), t0, 3, DARK).level - 0.5) < 0.01
    assert abs(run(fresh(t0), t0, 6, BRIGHT).level - 0.25) < 0.01
    assert run(fresh(t0), t0, 30, NORMAL).level == 1.0


def test_dark_but_moving_is_not_faster():
    t0 = datetime(2026, 11, 10, 8, 0)
    moving = {"ambient": 50, "gx": 20, "gy": 0, "gz": 0}
    assert abs(run(fresh(t0), t0, 6, moving).level - 0.5) < 0.01


def test_morning_resets_to_zero():
    """夜の「おやすみ」→ 暗い時間 → 明るいところで動き出したら 0"""
    night = datetime(2026, 11, 10, 22, 0)
    s = step(
        fresh(night, 0.9),
        P,
        night + timedelta(minutes=5),
        NORMAL,
        [(night + timedelta(minutes=3), "sleep")],
    )
    s = run(s, night + timedelta(minutes=5), 8, PITCH)
    assert abs(s.level - 0.9) < 0.02  # 暗いところで眠っているあいだは進まない
    morning = datetime(2026, 11, 11, 7, 0)
    s = run(s, _at(s), 1, NORMAL, {morning: "move"})
    assert s.level < 0.1 and s.night_since is None


def _at(s: RhythmState) -> datetime:
    return datetime.fromisoformat(s.ts)


def test_moving_in_the_dark_is_not_morning():
    """暗いまま動き出しただけでは朝にしない。明るくなって動いたときが朝"""
    night = datetime(2026, 11, 10, 22, 0)
    s = step(
        fresh(night, 0.8),
        P,
        night + timedelta(minutes=5),
        NORMAL,
        [(night + timedelta(minutes=3), "sleep")],
    )
    s = run(s, _at(s), 8, PITCH, {datetime(2026, 11, 11, 3, 0): "move"})
    assert s.level > 0.7 and s.night_since is not None
    s = run(s, _at(s), 2, NORMAL, {datetime(2026, 11, 11, 7, 0): "move"})
    assert s.level < 0.2


def test_day_rest_lowers_a_little_but_not_to_zero():
    """昼の「おやすみ」は止まるだけ。動き出したとき、休んだ長さに合わせて少し下がる"""
    noon = datetime(2026, 11, 10, 13, 0)
    s = step(
        fresh(noon, 0.5),
        P,
        noon + timedelta(minutes=5),
        BRIGHT,
        [(noon + timedelta(minutes=2), "sleep")],
    )
    before = run(s, _at(s), 1, BRIGHT)
    after = step(
        before,
        P,
        _at(before) + timedelta(minutes=5),
        BRIGHT,
        [(_at(before) + timedelta(minutes=1), "move")],
    )
    assert 0.85 * before.level < after.level < before.level  # 1 時間の休みで 1 割ほど
    long_rest = run(s, _at(s), 9, BRIGHT)
    woke = step(
        long_rest,
        P,
        _at(long_rest) + timedelta(minutes=5),
        BRIGHT,
        [(_at(long_rest) + timedelta(minutes=1), "move")],
    )
    assert abs(woke.level - long_rest.level * 0.8) < 0.02  # 長く休んでも、下がるのは上限(2 割)まで


def test_night_without_goodnight_is_found_by_pitch_dark():
    """「おやすみ」を書かなくても、まっくらな時間が続いたら夜。曇りの昼の暗さでは数えない"""
    evening = datetime(2026, 11, 10, 23, 0)
    s = run(fresh(evening, 0.9), evening, 6, PITCH)
    s = run(s, _at(s), 1, NORMAL, {datetime(2026, 11, 11, 5, 30): "move"})
    assert s.level < 0.1
    cloudy = run(fresh(datetime(2026, 11, 10, 13, 0), 0.4), datetime(2026, 11, 10, 13, 0), 6, DARK)
    cloudy = run(cloudy, _at(cloudy), 1, NORMAL, {datetime(2026, 11, 10, 19, 30): "move"})
    assert cloudy.level > 0.4


def test_long_gap_counts_as_a_night():
    """記録が長く抜けたら(家の PC が止まっていた夜)、夜をまたいだと数えて、明るければ 0 から"""
    s = fresh(datetime(2026, 11, 10, 18, 0), 0.9)
    s = step(s, P, datetime(2026, 11, 11, 12, 0), NORMAL, [])
    assert s.level == 0.0


def test_params_from_dict_ignores_bad_values():
    p = RhythmParams.from_dict({"hours": 14, "dark_x": -1, "bright_x": "x", "unknown": 3})
    assert p.hours == 14 and p.dark_x == 2.0 and p.bright_x == 0.5
    assert RhythmParams.from_dict(None) == RhythmParams()


def test_body_events_drop_brief_wake(tmp_path: Path):
    """体が起きても、10 分より短くまた眠ったら、動き出したと数えない。確かめるぶん、遅れて返す"""
    day = tmp_path / "20261113.jsonl"
    day.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"ts": "2026-11-13T15:05:31", "type": "woken"},
                {"ts": "2026-11-13T15:05:47", "type": "sleep"},
                {"ts": "2026-11-13T15:46:11", "type": "woken"},
            ]
        ),
        encoding="utf-8",
    )
    since, until = datetime(2026, 11, 13, 15, 0), datetime(2026, 11, 13, 15, 10)
    assert rhythm.body_events(tmp_path, since, until) == [
        (datetime(2026, 11, 13, 15, 5, 47), "sleep")
    ]
    assert (
        rhythm.body_events(tmp_path, datetime(2026, 11, 13, 15, 45), datetime(2026, 11, 13, 15, 50))
        == []
    )
    assert rhythm.body_events(
        tmp_path, datetime(2026, 11, 13, 15, 55), datetime(2026, 11, 13, 16, 0)
    ) == [(datetime(2026, 11, 13, 15, 46, 11), "move")]


def test_states_roundtrip(tmp_path: Path):
    path = tmp_path / "data" / "desire_rhythm.json"
    rhythm.save_states(path, {"want_to_sleep": RhythmState(level=0.4, ts="2026-11-13T10:00:00")})
    assert rhythm.load_states(path)["want_to_sleep"].level == 0.4
    assert rhythm.load_states(tmp_path / "none.json") == {}


def test_updater_uses_rhythm_only_for_marked_desires(tmp_path: Path):
    """rhythm を書いた欲求だけ新しい形で動き、ほかは今までどおり"""
    from desire_updater import DesireConfig, DesireSystemConfig, compute_desires, update_rhythms

    config = DesireSystemConfig(
        desires={
            "want_to_sleep": DesireConfig("眠い", "", 8.0, ["おやすみ"], rhythm={"hours": 12}),
            "curious": DesireConfig("知りたい", "", 6.0, ["調べた"]),
        },
        sensor_effects=[],
        cross_effects=[],
        priority=[],
    )
    db = tmp_path / "none.db"
    kw = dict(
        state_path=tmp_path / "desire_rhythm.json",
        experience_dir=tmp_path / "exp",
        session_log_dir=tmp_path / "logs",
    )
    t0 = datetime(2026, 11, 10, 8, 0)
    first = update_rhythms(db, config, NORMAL, now=t0, **kw)
    assert first == {"want_to_sleep": 1.0}  # はじめての回は、これまでの計算の値(記憶なし = 1.0)から
    states = rhythm.load_states(kw["state_path"])
    states["want_to_sleep"].level = 0.0
    rhythm.save_states(kw["state_path"], states)
    levels = update_rhythms(db, config, NORMAL, now=t0 + timedelta(hours=0.5), **kw)
    assert abs(levels["want_to_sleep"] - 0.5 / 12) < 0.001
    state = compute_desires(db, config, NORMAL, now=t0 + timedelta(hours=0.5), rhythm_levels=levels)
    assert state.desires["want_to_sleep"] == round(0.5 / 12, 3)
    assert state.desires["curious"] == 1.0  # rhythm の無い欲求は、今までどおり(記憶なし = 1.0)
    assert (
        update_rhythms(
            db,
            DesireSystemConfig({"curious": config.desires["curious"]}, [], [], []),
            NORMAL,
            now=t0,
            **kw,
        )
        == {}
    )
