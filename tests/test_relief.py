"""relief(体の出来事や言葉で、少しずつ満たされる欲求)のテスト"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import relief
from relief import WORDS, Happening, ReliefParams, ReliefState, step

T0 = datetime(2026, 11, 14, 9, 0)
P = ReliefParams.from_dict(
    {
        "hours": 48,
        "by": [
            {"event": "touch", "has": "strokes", "ratio": 0.6},
            {"event": "touch", "has": "taps", "ratio": 0.15, "cap": 0.4, "cap_hours": 6},
            {"event": "touch", "plain": True, "ratio": 0.3},
            {"event": "put_down", "min_carried_sec": 5, "ratio": 0.5},
            {"words": True, "ratio": 0.1, "cap": 0.3, "cap_hours": 12},
        ],
    },
    default_hours=8,
)


def fresh(level: float = 1.0, at: datetime = T0) -> ReliefState:
    return ReliefState(level=level, ts=at.isoformat(timespec="seconds"))


def touch(at: datetime, **detail: int) -> Happening:
    return Happening(at, "touch", {"count": 1, **detail})


def after(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_rises_with_time_to_the_top():
    s = step(fresh(0.0), P, T0 + timedelta(hours=24), [])
    assert s.level == 0.5
    s = step(s, P, T0 + timedelta(hours=60), [])
    assert s.level == 1.0


def test_stroke_takes_a_big_share_but_never_reaches_zero():
    s = step(fresh(1.0), P, after(5), [touch(after(1), strokes=1)])
    assert 0.39 < s.level < 0.41
    s = step(s, P, after(10), [touch(after(6), strokes=1)])
    assert 0.15 < s.level < 0.17  # 2 回目は 1 回目より小さく下がる
    for i in range(20):
        s = step(s, P, after(15 + 5 * i), [touch(after(11 + 5 * i), strokes=1)])
    assert s.level > 0


def test_tap_is_small():
    s = step(fresh(1.0), P, after(5), [touch(after(1), taps=1)])
    assert 0.84 < s.level < 0.86


def test_many_taps_never_beat_one_stroke():
    """短いタッチを何回重ねても(1 件に何回入っていても)、撫でられた 1 回を超えない"""
    s = fresh(1.0)
    for i in range(30):
        s = step(s, P, after(5 * (i + 1)), [touch(after(5 * i + 1), taps=9)])
    rise = 150 / 60 / 48
    assert s.level >= 0.6  # 上限は 4 割まで
    assert s.level <= 0.6 + rise + 0.001
    stroked = step(fresh(1.0), P, after(5), [touch(after(1), strokes=1)])
    assert stroked.level < s.level


def test_tap_cap_opens_again_after_the_window():
    s = fresh(1.0)
    for i in range(10):
        s = step(s, P, after(5 * (i + 1)), [touch(after(5 * i + 1), taps=1)])
    low = s.level
    s = step(s, P, T0 + timedelta(hours=8), [])
    s2 = step(
        s,
        P,
        T0 + timedelta(hours=8, minutes=5),
        [touch(T0 + timedelta(hours=8, minutes=1), taps=1)],
    )
    assert low < s.level
    assert s2.level < s.level * 0.86


def test_stroke_and_tap_in_one_event_both_count():
    s = step(fresh(1.0), P, after(5), [touch(after(1), strokes=1, taps=2)])
    assert 0.33 < s.level < 0.35  # 0.4 × 0.85


def test_plain_touch_is_for_bodies_that_do_not_tell_the_kind():
    s = step(fresh(1.0), P, after(5), [touch(after(1))])
    assert 0.69 < s.level < 0.71
    s = step(fresh(1.0), P, after(5), [touch(after(1), strokes=1)])
    assert 0.39 < s.level < 0.41  # 種類があれば plain の決まりは使わない


def test_held_counts_only_when_long_enough():
    short = Happening(after(1), "put_down", {"carried_sec": 2})
    long = Happening(after(1), "put_down", {"carried_sec": 8})
    assert step(fresh(1.0), P, after(5), [short]).level == 1.0  # 机の揺れは入れない
    assert 0.49 < step(fresh(1.0), P, after(5), [long]).level < 0.51


def test_words_are_weak_and_a_real_touch_after_them_still_counts():
    s = step(fresh(1.0), P, after(5), [Happening(after(1), WORDS)])
    assert 0.89 < s.level < 0.91
    for i in range(1, 10):  # たくさん書いても、半日で 3 割まで
        s = step(s, P, after(5 * (i + 1)), [Happening(after(5 * i + 1), WORDS)])
    assert s.level >= 0.7
    touched = step(s, P, after(60), [touch(after(56), strokes=1)])
    assert touched.level < s.level * 0.45


def test_same_happening_is_not_counted_twice():
    """記録は遅れて書かれるので、さかのぼって読む。同じ出来事を二度数えない"""
    h = touch(after(4), strokes=1)
    s = step(fresh(1.0), P, after(5), [h])
    again = step(s, P, after(10), [h])
    assert again.level > s.level  # 下がらず、時間で少し上がっただけ


def test_late_happening_is_still_counted():
    """前の 1 歩より前の時刻で、あとから届いた出来事も数える"""
    s = step(fresh(1.0), P, after(5), [])
    late = step(s, P, after(10), [touch(after(4), strokes=1)])
    assert late.level < 0.45


def test_unreadable_rules_are_skipped():
    p = ReliefParams.from_dict(
        {
            "by": [
                {"event": "touch", "ratio": 2},
                {"ratio": 0.5},
                "x",
                {"event": "touch", "ratio": 0.5},
            ]
        },
        default_hours=8,
    )
    assert p.hours == 8
    assert len(p.rules) == 1


def test_gather_reads_body_records_and_words(tmp_path: Path):
    exp = tmp_path / "experience"
    exp.mkdir()
    (exp / "20261114.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "ts": "2026-11-14T08:58:00",
                    "type": "touch",
                    "detail": {"count": 1, "strokes": 1},
                },
                {"ts": "2026-11-14T09:02:00", "type": "put_down", "detail": {"carried_sec": 7}},
                {"ts": "2026-11-14T09:03:00", "type": "ambient_jump", "detail": {}},
                {"ts": "2026-11-14T08:00:00", "type": "touch", "detail": {"count": 1}},
            ]
        )
        + "\nnot json\n",
        encoding="utf-8",
    )
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE memories (timestamp TEXT, content TEXT)")
    conn.execute("INSERT INTO memories VALUES ('2026-11-14T09:01:00', 'なでてもらった')")
    conn.execute("INSERT INTO memories VALUES ('2026-11-14T09:01:30', 'べつの話')")
    conn.commit()
    conn.close()
    got = relief.gather(P, exp, db, ["なで"], T0, after(5))
    assert sorted((h.ts.strftime("%H:%M"), h.kind) for h in got) == [
        ("08:58", "touch"),  # 10 分さかのぼって読む
        ("09:01", WORDS),
        ("09:02", "put_down"),
    ]


def test_states_roundtrip(tmp_path: Path):
    path = tmp_path / "data" / "desire_relief.json"
    s = step(fresh(1.0), P, after(5), [touch(after(1), taps=1)])
    relief.save_states(path, {"want_touch": s})
    back = relief.load_states(path)["want_touch"]
    assert back.level == s.level
    assert back.applied == s.applied
    assert relief.load_states(tmp_path / "none.json") == {}


# ---------------------------------------------------------------------------
# 中身で効き方を変える(where / scale_by)、間をあけて上げる(raise / delay_min)
# ---------------------------------------------------------------------------

P2 = ReliefParams.from_dict(
    {
        "hours": 48,
        "by": [
            {"event": "touch", "has": "strokes", "where": {"cam_to": "keeper"}, "ratio": 0.5},
            {
                "event": "arrived",
                "ratio": 0.2,
                "scale_by": "away_min",
                "full_at": 240,
                "scale_floor": 0.25,
                "cap": 0.2,
                "cap_hours": 12,
            },
            {"event": "arrived", "raise": 0.1, "delay_min": 15, "cap": 0.3, "cap_hours": 24},
        ],
    },
    default_hours=8,
)


def arrived(at: datetime, away_min: int | None = None) -> Happening:
    return Happening(
        at, "arrived", {"place": "つくえ"} if away_min is None else {"away_min": away_min}
    )


def test_where_matches_only_that_setting():
    mine = touch(after(1), strokes=1, cam_to="keeper")
    other = Happening(after(1), "touch", {"count": 1, "strokes": 1, "cam_to": "guest"})
    unknown = touch(after(1), strokes=1)
    assert 0.49 < step(fresh(1.0), P2, after(5), [mine]).level < 0.51
    assert step(fresh(1.0), P2, after(5), [other]).level == 1.0
    assert step(fresh(1.0), P2, after(5), [unknown]).level == 1.0


def test_longer_away_counts_more():
    """久しぶりに近づいたときほど大きく。10 分ぶりは小さく、4 時間ぶりは決まりどおり"""
    p = ReliefParams.from_dict(
        {
            "hours": 48,
            "by": [{"event": "arrived", "ratio": 0.2, "scale_by": "away_min", "full_at": 240}],
        },
        default_hours=8,
    )
    short = step(fresh(1.0), p, after(5), [arrived(after(1), 24)]).level
    long = step(fresh(1.0), p, after(5), [arrived(after(1), 480)]).level
    assert 0.97 < short < 0.99  # 0.2 × 24/240 = 0.02
    assert 0.79 < long < 0.81


def test_presence_alone_stays_within_its_cap():
    """気配だけでは、半日のうちに 2 割までしか下がらない"""
    s = fresh(1.0)
    for i in range(6):
        s = step(s, P2, after(60 * i + 5), [arrived(after(60 * i + 1), 600)])
    assert s.level >= 0.8


def test_scale_floor_when_away_time_is_unknown():
    s = step(fresh(1.0), P2, after(5), [arrived(after(1))])
    assert 0.94 < s.level < 0.96  # 0.2 × 0.25


def test_raise_comes_after_the_delay_and_is_capped():
    """気配のあと、少し間をあけてから上がる。1 日に上がる量には上限"""
    p = ReliefParams.from_dict(
        {
            "hours": 480,
            "by": [
                {"event": "arrived", "raise": 0.1, "delay_min": 15, "cap": 0.25, "cap_hours": 24}
            ],
        },
        default_hours=8,
    )
    h = arrived(after(1), 300)
    s = step(fresh(0.3), p, after(5), [h])
    assert s.level < 0.31  # まだ間をあけているところ
    s = step(s, p, after(10), [h])
    assert s.level < 0.31
    s = step(s, p, after(20), [h])
    assert 0.40 < s.level < 0.41  # 15 分たって、ふっと上がる
    s = step(s, p, after(25), [h])
    assert 0.40 < s.level < 0.41  # 同じ気配で二度は上がらない
    for i in range(1, 6):
        h2 = arrived(after(60 * i), 40)
        for m in (60 * i + 5, 60 * i + 20):
            s = step(s, p, after(m), [h2])
    assert s.level < 0.3 + 0.25 + 0.02  # 何度近づいても、1 日に 0.25 まで


def test_lowering_and_delayed_raise_from_one_happening():
    """1 つの気配で、すぐ少し下がり、あとで少し上がる(番号の違う決まりは別々に数える)"""
    h = arrived(after(1), 600)
    s = step(fresh(1.0), P2, after(5), [h])
    assert 0.79 < s.level < 0.81
    s2 = step(s, P2, after(20), [h])
    assert 0.90 < s2.level < 0.91


def test_old_done_keys_still_prevent_double_count():
    """前の版の覚え方(出来事ごと)が残っていても、二度数えない"""
    h = touch(after(4), strokes=1, cam_to="keeper")
    s = fresh(0.5)
    s.done = [h.key]
    assert step(s, P2, after(5), [h]).level > 0.5


def test_gather_reads_house_events_from_extra_dirs(tmp_path: Path):
    exp, house = tmp_path / "experience", tmp_path / "presence"
    exp.mkdir()
    house.mkdir()
    (house / "20261114.jsonl").write_text(
        json.dumps(
            {"ts": "2026-11-14T09:02:00", "type": "arrived", "place": "つくえ", "away_min": 95}
        )
        + "\n",
        encoding="utf-8",
    )
    got = relief.gather(P2, [exp, house, tmp_path / "none"], tmp_path / "none.db", [], T0, after(5))
    assert [(h.kind, h.detail["away_min"]) for h in got] == [("arrived", 95)]


def test_lookback_grows_with_delay():
    assert P.lookback == relief.LOOKBACK
    assert P2.lookback == relief.LOOKBACK + timedelta(minutes=15)
