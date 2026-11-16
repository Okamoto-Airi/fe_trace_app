"""日時のヘルパー。

方針: DB には「タイムゾーン情報なしの UTC」で保存し、
日付の判定（連続学習日数・グラフ）と画面表示のときだけ日本時間(JST)に変換する。
（本番サーバー=PythonAnywhere は UTC で動いており、既存データも UTC のため移行不要）
"""
from datetime import date, datetime, time, timedelta, timezone

JST = timezone(timedelta(hours=9))


def utcnow() -> datetime:
    """DB に保存する現在時刻（タイムゾーン情報なしの UTC）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_jst(ts: datetime) -> datetime:
    """DB の時刻（UTC）を日本時間に変換する"""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(JST)


def jst_today() -> date:
    """日本時間での「今日」"""
    return datetime.now(JST).date()


def jst_day_start_as_utc(day: date) -> datetime:
    """日本時間 day の 0:00 を、DB と比較できる UTC（tzなし）で返す"""
    return datetime.combine(day, time.min) - timedelta(hours=9)
