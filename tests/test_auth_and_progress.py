"""認証まわり（next / ログアウト / 二重送信）と学習進捗（重複なしの正解数）のテスト。

実行: python -m unittest discover -s tests -v
"""
import os
import re
import sys
import unittest
from urllib.parse import quote

# app をインポートする前に、テスト用の設定を環境変数で与える（.env より優先される）
os.environ["SECRET_KEY"] = "t" * 48
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402
from models import LearningLog, Problem, User, db  # noqa: E402

BASE = "https://localhost"  # SESSION_COOKIE_SECURE=True のため https で叩く


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.app.config["TESTING"] = True
        app_module._login_failures.clear()
        with self.app.app_context():
            db.create_all()
            db.session.add(
                User(
                    username="alice",
                    password=generate_password_hash(
                        "password123", method="pbkdf2:sha256"
                    ),
                )
            )
            for pid in ("p1", "p2"):
                db.session.add(
                    Problem(
                        id=pid,
                        mode="practice",
                        title=pid,
                        code_text="x",
                        data_json="{}",
                    )
                )
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # --- ヘルパー ---
    def get(self, path):
        return self.client.get(path, base_url=BASE)

    def html(self, path):
        return self.get(path).get_data(as_text=True)

    def status(self, path):
        return self.get(path).status_code

    def token(self, path="/login"):
        return _csrf(self.html(path))

    def login(self, url="/login", username="alice", password="password123"):
        return self.client.post(
            url,
            data={
                "username": username,
                "password": password,
                "csrf_token": self.token(url),
            },
            base_url=BASE,
        )


class NextParameterTests(AppTestCase):
    def test_login_required_page_redirects_back_after_login(self):
        r = self.client.get("/practice/p1", base_url=BASE)
        self.assertEqual(r.status_code, 302)
        login_url = r.headers["Location"]
        self.assertIn("next=%2Fpractice%2Fp1", login_url)

        r = self.login(login_url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/practice/p1")

    def test_login_without_next_goes_home(self):
        r = self.login("/login")
        self.assertEqual(r.headers["Location"], "/home")

    def test_unsafe_next_is_ignored(self):
        for evil in (
            "//evil.example",
            "https://evil.example",
            "/\\evil.example",
            "/\t/evil.example",
            "javascript:alert(1)",
            "evil.example",
        ):
            with self.subTest(next=evil):
                self.client = self.app.test_client()
                app_module._login_failures.clear()
                r = self.login(f"/login?next={quote(evil, safe='')}")
                self.assertEqual(r.status_code, 302)
                self.assertEqual(r.headers["Location"], "/home")

    def test_next_survives_move_to_register_and_back(self):
        html = self.html("/login?next=%2Fpractice%2Fp1")
        # "/" はエンコードされる場合とされない場合があり、どちらも有効なURL
        self.assertRegex(
            html, r'/register\?next=(?:%2F|/)practice(?:%2F|/)p1"'
        )

        r = self.client.post(
            "/register?next=%2Fpractice%2Fp1",
            data={
                "username": "bob",
                "password": "password123",
                "csrf_token": self.token("/register"),
            },
            base_url=BASE,
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/practice/p1")


class LogoutTests(AppTestCase):
    def test_get_logout_is_not_allowed_and_keeps_session(self):
        self.login()
        r = self.get("/logout")
        self.assertEqual(r.status_code, 405)
        self.assertEqual(self.status("/home"), 200)

    def test_post_logout_without_csrf_is_rejected(self):
        self.login()
        r = self.client.post("/logout", base_url=BASE)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.status("/home"), 200)

    def test_post_logout_with_csrf_logs_out(self):
        self.login()
        token = _csrf(self.html("/home"))
        r = self.client.post(
            "/logout", data={"csrf_token": token}, base_url=BASE
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.status("/home"), 302)

    def test_nav_has_logout_form_not_link(self):
        self.login()
        html = self.html("/home")
        self.assertRegex(html, r'<form\s+action="/logout"\s+method="POST"')
        self.assertNotIn('href="/logout"', html)


class SubmitOnceTests(AppTestCase):
    def test_login_and_register_forms_prevent_double_submit(self):
        for path in ("/login", "/register"):
            with self.subTest(path=path):
                self.assertIn("data-submit-once", self.html(path))
        # 共通スクリプトは base.html にある
        self.assertIn("form[data-submit-once]", self.html("/login"))


class SolvedCountTests(AppTestCase):
    def add_log(self, problem_id, correct):
        with self.app.app_context():
            uid = User.query.filter_by(username="alice").one().id
            db.session.add(
                LearningLog(
                    user_id=uid,
                    problem_id=problem_id,
                    mode="practice",
                    is_correct=correct,
                )
            )
            db.session.commit()
            return uid

    def solved(self):
        with self.app.app_context():
            uid = User.query.filter_by(username="alice").one().id
            return app_module.count_solved_problems(uid)

    def test_same_problem_solved_repeatedly_counts_once(self):
        for _ in range(5):
            self.add_log("p1", True)
        self.assertEqual(self.solved(), 1)

    def test_wrong_answers_are_not_counted(self):
        self.add_log("p1", False)
        self.assertEqual(self.solved(), 0)

    def test_different_problems_are_counted_separately(self):
        self.add_log("p1", True)
        self.add_log("p1", True)
        self.add_log("p2", True)
        self.assertEqual(self.solved(), 2)

    def test_account_page_shows_unique_count(self):
        for _ in range(3):
            self.add_log("p1", True)
        self.login()
        html = self.html("/account")
        self.assertRegex(html, r">\s*1 問\s*<")


class StaticAssetTests(AppTestCase):
    def test_tailwind_is_served_locally_not_from_cdn(self):
        html = self.html("/login")
        self.assertNotIn("cdn.tailwindcss.com", html)
        self.assertIn("/static/css/tailwind.css", html)
        r = self.get("/static/css/tailwind.css")
        self.assertEqual(r.status_code, 200)
        r.close()


if __name__ == "__main__":
    unittest.main()
