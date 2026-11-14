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
