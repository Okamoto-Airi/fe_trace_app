"""日本時間(JST)での日付判定、ホームの集計、favicon のテスト。

DB には UTC（tzなし）で保存し、日付の判定と表示のときだけ JST にする。
JST = UTC + 9時間 なので、UTC 15:00 以降は JST では翌日になる。

実行: python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta

# 単独実行（python -m unittest tests.test_time_and_home）でも同じ階層を読めるように
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_auth_and_progress import AppTestCase  # noqa: E402 環境変数の設定もここで行われる
import app as app_module
from models import LearningLog, User, db
from timeutil import jst_day_start_as_utc, to_jst, utcnow

TODAY = date(2026, 11, 16)  # テストで「今日(JST)」とみなす日


def utc(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute)


class TimeHelperTests(unittest.TestCase):
    def test_utc_afternoon_is_next_day_in_jst(self):
        self.assertEqual(
            to_jst(utc(11, 15, 15, 0)).replace(tzinfo=None),
            datetime(2026, 11, 16, 0, 0),
        )
        self.assertEqual(
            to_jst(utc(11, 15, 14, 59)).date(), date(2026, 11, 15)
        )

    def test_jst_day_start_as_utc(self):
        self.assertEqual(jst_day_start_as_utc(TODAY), utc(11, 15, 15, 0))

    def test_utcnow_is_naive(self):
        self.assertIsNone(utcnow().tzinfo)


class HomeAggregationTestCase(AppTestCase):
    def user_id(self):
        with self.app.app_context():
            return User.query.filter_by(username="alice").one().id

    def add_log(self, when, correct=True, mode="practice", problem="p1"):
        with self.app.app_context():
            uid = User.query.filter_by(username="alice").one().id
            db.session.add(
                LearningLog(
                    user_id=uid,
                    problem_id=problem,
                    mode=mode,
                    is_correct=correct,
                    timestamp=when,
                )
            )
            db.session.commit()

    def streak(self, today=TODAY):
        with self.app.app_context():
            return app_module.calculate_streak(self.user_id(), today)


class StreakTests(HomeAggregationTestCase):
    def test_no_logs(self):
        self.assertEqual(self.streak(), 0)

    def test_two_jst_days_that_share_one_utc_date(self):
        # UTC では同じ 11/15 だが、JST では 11/15 19:00 と 11/16 01:00
        self.add_log(utc(11, 15, 10))
        self.add_log(utc(11, 15, 16))
        self.assertEqual(self.streak(), 2)

    def test_streak_continues_when_not_yet_studied_today(self):
        self.add_log(utc(11, 15, 3))  # JST 11/15
        self.add_log(utc(11, 14, 3))  # JST 11/14
        self.assertEqual(self.streak(), 2)

    def test_gap_breaks_streak(self):
        self.add_log(utc(11, 15, 16))  # JST 11/16
        self.add_log(utc(11, 13, 16))  # JST 11/14（11/15 が空く）
        self.assertEqual(self.streak(), 1)

    def test_last_study_two_days_ago_is_zero(self):
        self.add_log(utc(11, 14, 3))  # JST 11/14
        self.assertEqual(self.streak(), 0)

    def test_several_logs_in_a_day_count_once(self):
        for hour in (1, 2, 3):
            self.add_log(utc(11, 15, hour))  # JST 11/15
        self.add_log(utc(11, 14, 3))  # JST 11/14
        self.assertEqual(self.streak(), 2)

    def test_pagination_does_not_duplicate_days(self):
        # ページの境目をまたいでも、同じ日を二重に返さない
        for day, hour in ((15, 1), (15, 2), (15, 3), (14, 1), (14, 2)):
            self.add_log(utc(11, day, hour))
        with self.app.app_context():
            days = list(
                app_module._studied_dates_desc(self.user_id(), page_size=2)
            )
        self.assertEqual(days, [date(2026, 11, 15), date(2026, 11, 14)])


class WeeklyGraphTests(HomeAggregationTestCase):
    def graph(self):
        with self.app.app_context():
            return app_module.weekly_correct_counts(self.user_id(), TODAY)

    def test_labels_cover_seven_days_ending_today(self):
        labels, _, _ = self.graph()
        self.assertEqual(
            labels,
            ["11/10", "11/11", "11/12", "11/13", "11/14", "11/15", "11/16"],
        )

    def test_counts_use_jst_days_and_modes(self):
        self.add_log(utc(11, 15, 15, 30))  # JST 11/16 00:30 → 11/16 に数える
        self.add_log(utc(11, 16, 0), mode="exam", problem="p2")  # JST 11/16
        self.add_log(utc(11, 15, 14, 59))  # JST 11/15 23:59 → 11/15
        _, practice, exam = self.graph()
        self.assertEqual(practice, [0, 0, 0, 0, 0, 1, 1])
        self.assertEqual(exam, [0, 0, 0, 0, 0, 0, 1])

    def test_incorrect_answers_are_ignored(self):
        self.add_log(utc(11, 16, 1), correct=False)
        _, practice, exam = self.graph()
        self.assertEqual(sum(practice) + sum(exam), 0)

    def test_window_boundary(self):
        self.add_log(utc(11, 9, 14, 59))  # JST 11/09 23:59 → 範囲外
        self.add_log(utc(11, 9, 15, 0))  # JST 11/10 00:00 → 範囲内の初日
        _, practice, _ = self.graph()
        self.assertEqual(practice, [1, 0, 0, 0, 0, 0, 0])


class HomePageTests(HomeAggregationTestCase):
    def test_recent_history_is_shown_in_jst(self):
        self.add_log(utc(11, 15, 15, 30))  # JST 2026/11/16 00:30
        self.login()
        html = self.html("/home")
        self.assertIn("2026/11/16 00:30", html)
        self.assertNotIn("2026/11/15 15:30", html)

    def test_home_works_without_logs(self):
        self.login()
        self.assertEqual(self.status("/home"), 200)

    def test_default_timestamp_is_utc(self):
        with self.app.app_context():
            uid = User.query.filter_by(username="alice").one().id
            log = LearningLog(user_id=uid, problem_id="p1", mode="practice")
            db.session.add(log)
            db.session.commit()
            gap = abs(log.timestamp - utcnow())
            self.assertLess(gap, timedelta(seconds=5))


class FaviconTests(AppTestCase):
    def test_favicon_is_served(self):
        r = self.get("/favicon.ico")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "image/vnd.microsoft.icon")
        self.assertTrue(r.data.startswith(b"\x00\x00\x01\x00"))  # ICOヘッダ
        r.close()

    def test_pages_link_to_the_favicon(self):
        self.assertIn('rel="icon"', self.html("/login"))


if __name__ == "__main__":
    unittest.main()
