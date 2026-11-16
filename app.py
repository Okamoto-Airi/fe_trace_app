# pyright: reportCallIssue=false, reportAttributeAccessIssue=false
# （Flask-SQLAlchemy のモデル引数や LoginManager.login_view に対する型チェッカーの誤検知を抑止）
import os  # OS関連の操作を行うためのモジュールをインポート
import re  # 正規表現（ユーザー名の検証用）
import json  # JSONデータの操作を行うためのモジュールをインポート
import time  # ログイン試行回数制限用
import hmac  # CSRFトークンの定数時間比較用
import secrets  # CSRFトークン生成用
import logging  # 警告ログ出力用
from collections import defaultdict, deque  # ログイン試行履歴の保持用
import click  # Flask CLIコマンドを作成するためのモジュールをインポート

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    abort,
    send_from_directory,
    url_for,
    flash,
    jsonify,
)  # Flaskの主要な機能をインポート
from flask.cli import with_appcontext  # Flask CLIのコンテキストデコレータをインポート
from flask_login import (  # Flask-Loginの機能をインポート
    LoginManager,  # ログイン管理クラス
    login_user,  # ユーザーログイン関数
    login_required,  # ログイン必須デコレータ
    logout_user,  # ユーザーログアウト関数
    current_user,  # 現在のユーザーオブジェクト
)
from sqlalchemy import distinct, func  # 重複を除いた集計用
from sqlalchemy.exc import IntegrityError  # 登録時の重複競合を扱う
from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)  # パスワードハッシュ化と検証関数をインポート
from datetime import timedelta  # 日時操作のためのモジュールをインポート
from urllib.parse import urlparse  # next パラメータの検証用
from dotenv import load_dotenv  # 環境変数をロードするためのモジュールをインポート

# models.py からインポート
from models import db, User, Problem, LearningLog  # データベースモデルをインポート
from timeutil import jst_day_start_as_utc, jst_today, to_jst  # 日時（JST変換）


# .envファイルをロード (最初に行う)
load_dotenv()

app = Flask(__name__)  # Flaskアプリケーションインスタンスを作成
# SECRET_KEY は環境変数から取得（なければランタイムで安全な一時キーを生成）
secret = os.getenv("SECRET_KEY")
if not secret:
    # 開発時はランダムキーを生成（再起動で切り替わるため本番ではENVに設定必須）
    secret = os.urandom(32).hex()
    logging.getLogger(__name__).warning(
        "SECRET_KEY が未設定です。一時キーを使用するため、再起動やワーカー間でセッションが無効になります。"
        "本番では十分長いランダム値を環境変数 SECRET_KEY に設定してください。"
    )
elif len(secret) < 32:
    logging.getLogger(__name__).warning(
        "SECRET_KEY が短すぎます（32文字以上のランダム値を推奨）。"
    )
app.config["SECRET_KEY"] = secret

# 基本設定
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.getenv(  # データベースURIを設定（環境変数から取得、なければSQLiteデフォルト）
        "DATABASE_URL", "sqlite:///database.db"
    )
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # SQLAlchemyの変更追跡を無効化

# セキュリティ関連のCookie/URL設定
# 本番ではHTTPSを必須とし、セッションCookieを安全にする
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_SECURE"] = True
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["PREFERRED_URL_SCHEME"] = "https"
# リクエストボディの上限（巨大入力による負荷対策）
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1MB
# セッションの有効期間
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

db.init_app(app)  # SQLAlchemyをFlaskアプリに初期化
login_manager = LoginManager()  # LoginManagerインスタンスを作成
login_manager.init_app(app)  # LoginManagerをFlaskアプリに初期化
login_manager.login_view = (
    "login"  # ログインが必要なページでリダイレクトするビューを設定
)
# "strong" だとモバイル回線で IP が変わるたびにログアウトされてしまうため "basic" にする。
# （"basic" は IP/UA の変化時にセッションを「非fresh」扱いにするのみ。盗用対策は
#   HTTPOnly/Secure/SameSite Cookie と HTTPS で担保）
login_manager.session_protection = "basic"


# 本番環境では HTTPS を強制し、HSTS ヘッダを付与する
@app.before_request
def enforce_https_in_production():
    if not app.debug and not app.testing:
        # リバースプロキシが X-Forwarded-Proto をセットしている場合に対応
        proto = request.headers.get("X-Forwarded-Proto", "")
        if proto and proto != "https":
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)
        # 直接アクセスの場合
        if not proto and not request.is_secure:
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)


@app.after_request
def set_security_headers(response):
    # HSTS を付与（本番のみ）
    if not app.debug and not app.testing:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains; preload",
        )
    # クリックジャッキング・MIMEスニッフィング等への対策
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Referrer-Policy", "strict-origin-when-cross-origin"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'; object-src 'none'",
    )
    return response


# ---------------------------------------------------------
# CSRF 対策（追加パッケージ不要の簡易実装）
#  - フォーム: hidden フィールド "csrf_token"
#  - fetch/XHR: ヘッダー "X-CSRFToken"
# ---------------------------------------------------------
def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = get_csrf_token


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get("_csrf_token", "")
        sent = request.headers.get("X-CSRFToken") or request.form.get(
            "csrf_token", ""
        )
        if not expected or not sent or not hmac.compare_digest(expected, sent):
            abort(400, description="CSRF token missing or invalid")


# ---------------------------------------------------------
# ログイン試行回数制限（プロセス内メモリ。ワーカーごとのベストエフォート）
# ---------------------------------------------------------
LOGIN_MAX_ATTEMPTS = 5  # 窓内の失敗上限
LOGIN_WINDOW_SEC = 15 * 60  # 15分
_login_failures = defaultdict(deque)


def _client_ip():
    # X-Forwarded-For の「先頭」はクライアントが自由に偽装できる。
    # 信頼できるのは、手前のプロキシ(PythonAnywhere)が末尾に追記した値なので、
    # 最後の要素を使う。ヘッダーが無ければ接続元アドレスを使う。
    fwd = request.headers.get("X-Forwarded-For", "")
    parts = [p.strip() for p in fwd.split(",") if p.strip()]
    ip = parts[-1] if parts else request.remote_addr
    return ip or "unknown"


def _login_key(username):
    return (_client_ip(), (username or "").lower()[:150])


def _is_locked(key):
    q = _login_failures[key]
    now = time.time()
    while q and now - q[0] > LOGIN_WINDOW_SEC:
        q.popleft()
    if not q:
        _login_failures.pop(key, None)
    return len(q) >= LOGIN_MAX_ATTEMPTS


# 存在しないユーザーでも同程度の処理時間にするためのダミーハッシュ
_DUMMY_HASH = generate_password_hash("dummy-password", method="pbkdf2:sha256")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,50}$")


# ---------------------------------------------------------
# 管理用コマンド (ターミナルで実行: flask seed-db)
# ---------------------------------------------------------
@click.command("init-db")  # Flask CLIコマンドを定義（init-db）
@with_appcontext  # Flaskアプリコンテキスト内で実行
def init_db_command():  # データベース初期化コマンド関数
    """DBテーブルを新規作成"""
    db.create_all()  # すべてのテーブルを作成
    click.echo("Initialized the database.")  # 成功メッセージを表示


@click.command("seed-db")  # Flask CLIコマンドを定義（seed-db）
@with_appcontext  # Flaskアプリコンテキスト内で実行
def seed_db_command():  # データベースシードコマンド関数
    """JSONファイルから問題データを読み込みDBへ同期"""
    json_path = os.path.join(
        app.root_path, "data", "questions.json"
    )  # JSONファイルのパスを構築

    if not os.path.exists(json_path):  # JSONファイルが存在しない場合
        click.echo(f"Error: {json_path} not found.")  # エラーメッセージを表示
        return  # 関数を終了

    with open(json_path, "r", encoding="utf-8") as f:  # JSONファイルを読み込み
        problems_list = json.load(f)  # JSONデータをリストとしてロード

    count = 0  # 処理した問題数をカウント
    for p_data in problems_list:  # 各問題データをループ

        # IDで検索し、あれば更新、なければ新規作成
        problem = Problem.query.get(p_data["id"])  # IDで問題を検索
        if not problem:  # 存在しない場合
            problem = Problem(id=p_data["id"])  # 新しいProblemインスタンスを作成

        # データの流し込み
        problem.mode = p_data["mode"]  # モードを設定
        problem.title = p_data["title"]  # タイトルを設定
        problem.category = p_data.get(
            "category", "未分類"
        )  # カテゴリを設定（デフォルト"未分類"）
        problem.difficulty = p_data.get(
            "difficulty", "標準"
        )  # 難易度を設定（デフォルト"標準"）

        # 変数リストはカンマ区切り文字列にして保存
        problem.variables_str = ",".join(p_data.get("variables", []))
        # 構造データ(steps/options)はJSON文字列にして保存
        problem.data_json = json.dumps(
            p_data.get("data", {}), ensure_ascii=False
        )

        if "content" in p_data:
            # 1. content をそのまま保存
            problem.content_json = json.dumps(
                p_data["content"], ensure_ascii=False
            )

            # 2. code_text カラムへのバックフィル (DBのNot Null制約回避 & 検索用)
            # content 内の type="code" のブロックを探す
            code_block = next(
                (
                    item
                    for item in p_data["content"]
                    if item.get("type") == "code"
                ),
                None,
            )
            if code_block:
                problem.code_text = code_block["text"]  # 新JSONのキーは "text"
            else:
                # コードがない問題は稀だが、空文字を入れておく
                problem.code_text = ""

            # 3. description カラムへのバックフィル
            # 最初のテキストブロックを説明文として保存しておく
            text_block = next(
                (
                    item
                    for item in p_data["content"]
                    if item.get("type") == "text"
                ),
                None,
            )
            problem.description = text_block["text"] if text_block else ""

        else:
            # 練習モードなど、従来の形式 (contentがない場合)
            problem.code_text = p_data["code_text"]
            # 練習モードには description がない場合がある
            problem.description = p_data.get("description", "")
            problem.content_json = "[]"  # 空のリスト

        db.session.add(problem)  # セッションに追加
        count += 1  # カウントを増やす

    db.session.commit()  # 変更をコミット
    click.echo(
        f"Successfully seeded {count} problems from JSON."
    )  # 成功メッセージを表示


# コマンド登録
app.cli.add_command(init_db_command)  # init-dbコマンドを登録
app.cli.add_command(seed_db_command)  # seed-dbコマンドを登録


@login_manager.user_loader  # ユーザー読み込み関数をデコレータで登録
def load_user(user_id):  # ユーザーIDからユーザーをロードする関数
    return User.query.get(int(user_id))  # IDでUserをクエリして返す


def _safe_next_url(target):
    """ログイン後のリダイレクト先として安全な「サイト内の相対パス」だけを返す。

    外部サイトへ飛ばされるオープンリダイレクトを防ぐため、
    "//evil.com" や "https://evil.com"、"/\\evil.com"、制御文字を含む値は None にする。
    """
    if not isinstance(target, str) or not target.startswith("/"):
        return None
    if target.startswith("//") or "\\" in target:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


def count_solved_problems(user_id: int) -> int:
    """正解した「問題の種類数」を返す（同じ問題を何度正解しても1と数える）"""
    return (
        db.session.query(func.count(distinct(LearningLog.problem_id)))
        .filter(LearningLog.user_id == user_id, LearningLog.is_correct.is_(True))
        .scalar()
        or 0
    )


def _studied_dates_desc(user_id: int, page_size: int = 200):
    """学習した日付(JST)を新しい順に、重複なしで返す。

    全ログを一度に読まず、ページ単位で取得する。呼び出し側が途中で
    打ち切れば、それ以降は読まない（連続日数の計算は途切れた時点で終わる）。
    """
    query = (
        db.session.query(LearningLog.timestamp)
        .filter(LearningLog.user_id == user_id)
        .order_by(LearningLog.timestamp.desc())
    )
    previous = None
    offset = 0
    while True:
        page = query.offset(offset).limit(page_size).all()
        if not page:
            return
        for (ts,) in page:
            day = to_jst(ts).date()
            if day != previous:
                previous = day
                yield day
        offset += page_size


def calculate_streak(user_id: int, today) -> int:
    """連続学習日数。今日まだ学習していなくても、昨日までの連続は途切れない。"""
    streak = 0
    expected = None
    for day in _studied_dates_desc(user_id):
        if day > today:  # 未来の日付は無視
            continue
        if streak == 0:
            if day < today - timedelta(days=1):  # 昨日より前 → 連続なし
                break
            expected = day  # 今日 または 昨日 から数え始める
        if day != expected:
            break
        streak += 1
        expected = day - timedelta(days=1)
    return streak


def weekly_correct_counts(user_id: int, today):
    """過去7日間(今日を含む)の、モード別の正解数を返す。

    戻り値: (日付ラベル["11/16", ...], 練習の正解数リスト, 過去問の正解数リスト)
    直近7日分の正解ログだけをDBから取得する。
    """
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    rows = (
        db.session.query(LearningLog.timestamp, LearningLog.mode)
        .filter(
            LearningLog.user_id == user_id,
            LearningLog.is_correct.is_(True),
            LearningLog.timestamp >= jst_day_start_as_utc(days[0]),
        )
        .all()
    )
    counts = {day: {"practice": 0, "exam": 0} for day in days}
    for ts, mode in rows:
        day = to_jst(ts).date()
        if day in counts and mode in counts[day]:
            counts[day][mode] += 1

    labels = [day.strftime("%m/%d") for day in days]
    return (
        labels,
        [counts[day]["practice"] for day in days],
        [counts[day]["exam"] for day in days],
    )


@app.template_filter("jst")
def format_jst(ts, fmt="%Y/%m/%d %H:%M"):
    """テンプレート用: DBのUTC時刻を日本時間の文字列にする"""
    return to_jst(ts).strftime(fmt)


def calculate_rank(solved_count: int):
    """
    解いた問題数から称号とCSSクラスを返す
    """
    if solved_count >= 30:
        return "トレースレジェンド", "text-yellow-500"
    elif solved_count >= 25:
        return "トレースマスター", "text-purple-500"
    elif solved_count >= 20:
        return "トレース職人", "text-red-500"
    elif solved_count >= 10:
        return "トレース上級者", "text-sky-500"
    else:
        return "トレース見習い", "text-lime-600"

# --- ルーティング ---


@app.route("/favicon.ico")
def favicon():
    # ブラウザは /favicon.ico を直接取りに来ることがある（404ログの原因）
    return send_from_directory(
        app.static_folder,
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
        max_age=7 * 24 * 3600,
    )


@app.route("/")  # ルートURLのルーティング
def index():  # インデックス関数
    # ログイン済みならホームへ
    if current_user.is_authenticated:  # ユーザーが認証済みの場合
        return redirect(url_for("home"))  # ホームへリダイレクト
    # 未ログインなら、明示的に /login へリダイレクトする
    return redirect(url_for("login"))  # ログインへリダイレクト


@app.route("/home")
@login_required
def home():
    today = jst_today()  # 日付の判定はすべて日本時間

    streak = calculate_streak(current_user.id, today)

    # 称号: 正解した問題の種類数（同じ問題を何度正解しても1問）
    solved_count = count_solved_problems(current_user.id)
    rank, rank_color = calculate_rank(solved_count)

    # グラフ用データ (過去7日間)
    graph_labels, data_practice, data_exam = weekly_correct_counts(
        current_user.id, today
    )

    # 最近の履歴（5件だけ取得する）
    recent_logs = (
        LearningLog.query.filter_by(user_id=current_user.id)
        .order_by(LearningLog.timestamp.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "home.html",
        streak=streak,
        rank=rank,
        rank_color=rank_color,
        recent_logs=recent_logs,
        graph_labels=graph_labels,
        data_practice=data_practice,
        data_exam=data_exam,
    )  # ホームテンプレートをレンダリング


@app.route("/problems/<mode>")  # /problems/<mode> URLのルーティング
@login_required  # ログイン必須
def problem_list(mode):  # 問題リスト関数
    # 辞書ではなくDBからフィルタリングして取得
    if mode not in ["practice", "exam"]:  # モードが有効でない場合
        return redirect(url_for("home"))  # ホームへリダイレクト

    problems = Problem.query.filter_by(mode=mode).all()  # 指定モードの問題をクエリ

    # 称号計算用 (ヘッダー表示用): 正解した問題の種類数
    solved_count = count_solved_problems(current_user.id)

    # 最近の履歴
    recent_logs = (  # 最近のログをクエリ
        LearningLog.query.filter_by(user_id=current_user.id)  # 現在のユーザーのログ
        .order_by(LearningLog.timestamp.desc())  # 降順ソート
        .limit(5)  # 5件に制限
        .all()
    )

    return render_template(  # 問題リストテンプレートをレンダリング
        "problem_list.html",
        problems=problems,
        mode=mode,
        solved_count=solved_count,
        recent_logs=recent_logs,
    )


@app.route("/practice/<problem_id>")  # /practice/<problem_id> URLのルーティング
@login_required  # ログイン必須
def practice(problem_id):  # 練習関数
    # DBから取得 (存在しなければ404)
    problem = Problem.query.get_or_404(problem_id)  # IDで問題を取得、存在しなければ404

    if problem.mode != "practice":  # モードがpracticeでない場合
        return redirect(url_for("home"))  # ホームへリダイレクト

    # テンプレートに渡す際、problemオブジェクトをそのまま渡す
    # (models.py で定義した @property def data により、テンプレート側で problem.data.steps のようにアクセス可能)  # noqa: E501
    return render_template(
        "practice.html", problem=problem
    )  # 練習テンプレートをレンダリング


@app.route("/exam/<problem_id>")  # /exam/<problem_id> URLのルーティング
@login_required  # ログイン必須
def exam(problem_id):  # 試験関数
    # DBから取得
    problem = Problem.query.get_or_404(problem_id)  # IDで問題を取得、存在しなければ404

    if problem.mode != "exam":  # モードがexamでない場合
        return redirect(url_for("home"))  # ホームへリダイレクト

    return render_template(
        "exam.html", problem=problem
    )  # 試験テンプレートをレンダリング


@app.route("/account")
@login_required
def account():
    solved_count = count_solved_problems(current_user.id)

    # 称号計算
    rank, rank_color = calculate_rank(solved_count)

    return render_template(
        "profile.html",
        solved_count=solved_count,
        rank=rank,
        rank_color=rank_color
    )


@app.route(
    "/account/delete", methods=["POST"]
)  # /account/delete URLのルーティング（POSTメソッド）
@login_required  # ログイン必須
def delete_account():  # アカウント削除関数
    # 関連データの削除
    LearningLog.query.filter_by(
        user_id=current_user.id
    ).delete()  # ユーザーのログを削除
    user = User.query.get(current_user.id)  # ユーザーを取得
    db.session.delete(user)  # ユーザーを削除
    db.session.commit()  # コミット
    logout_user()  # ログアウト
    flash("アカウントを削除しました。", "info")  # フラッシュメッセージ
    return redirect(url_for("index"))  # インデックスへリダイレクト


# --- API ---
@app.route(
    "/api/log_result", methods=["POST"]
)  # /api/log_result URLのルーティング（POSTメソッド）
@login_required  # ログイン必須
def log_result():  # 結果ログ関数
    data = request.get_json(silent=True)  # JSONデータを取得（不正な形式でも例外にしない）
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "invalid request"}), 400

    problem_id = data.get("problem_id")
    if not isinstance(problem_id, str):
        return (
            jsonify({"status": "error", "message": "invalid problem_id"}),
            400,
        )
    problem = db.session.get(Problem, problem_id)  # 実在する問題のみ受け付ける
    if problem is None:
        return jsonify({"status": "error", "message": "unknown problem"}), 400

    is_correct = data.get("is_correct", False)
    if not isinstance(is_correct, bool):
        return (
            jsonify({"status": "error", "message": "invalid is_correct"}),
            400,
        )

    # モードはクライアント申告ではなく問題側の値を採用する
    new_log = LearningLog(  # 新しいLearningLogインスタンスを作成
        user_id=current_user.id,  # ユーザーID
        problem_id=problem.id,  # 問題ID
        mode=problem.mode,  # モード
        is_correct=is_correct,  # 正解フラグ
    )
    db.session.add(new_log)  # セッションに追加
    db.session.commit()  # コミット
    return jsonify({"status": "success"})  # 成功レスポンスをJSONで返す


# --- 認証系 (Login/Register/Logout) ---
# ※ app.py (Batch 1) のロジックと同様のため省略しますが、
#    login_user(user) を呼んだ後、redirect(url_for('home')) に遷移させてください。


@app.route(
    "/register", methods=["GET", "POST"]
)  # /register URLのルーティング（GET/POSTメソッド）
def register():  # 登録関数
    next_url = _safe_next_url(request.args.get("next"))  # 登録後の戻り先
    if request.method == "POST":  # POSTリクエストの場合
        username = request.form.get("username", "").strip()  # ユーザー名を取得
        password = request.form.get("password", "")  # パスワードを取得
        if not USERNAME_RE.match(username):
            flash("ユーザー名は3〜50文字の英数字と . _ - のみ使用できます", "error")
        elif not (8 <= len(password) <= 128):
            flash("パスワードは8〜128文字で入力してください", "error")
        elif User.query.filter_by(
            username=username
        ).first():  # ユーザー名が既に存在する場合
            flash("そのユーザー名は既に使用されています", "error")  # エラーフラッシュ
        else:  # 存在しない場合
            new_user = User(  # 新しいUserインスタンスを作成
                username=username,  # ユーザー名
                password=generate_password_hash(
                    password, method="pbkdf2:sha256"
                ),  # パスワードをハッシュ化
            )
            db.session.add(new_user)  # セッションに追加
            try:
                db.session.commit()  # コミット
            except IntegrityError:  # 同時登録による重複
                db.session.rollback()
                flash("そのユーザー名は既に使用されています", "error")
                return render_template("register.html", next_url=next_url)
            session.clear()  # ログイン前にセッションを破棄（セッション固定化対策）
            session.permanent = True  # PERMANENT_SESSION_LIFETIME(7日)を有効にする
            login_user(new_user)  # ログイン
            return redirect(next_url or url_for("home"))  # 元のページ or ホームへ
    return render_template(
        "register.html", next_url=next_url
    )  # 登録テンプレートをレンダリング


@app.route(
    "/login", methods=["GET", "POST"]
)  # /login URLのルーティング（GET/POSTメソッド）
def login():  # ログイン関数
    next_url = _safe_next_url(request.args.get("next"))  # ログイン後の戻り先
    if request.method == "POST":  # POSTリクエストの場合
        username = request.form.get("username", "")[:150]  # ユーザー名を取得
        password = request.form.get("password", "")[:128]  # パスワードを取得（長さ上限）
        key = _login_key(username)
        if _is_locked(key):
            flash("ログイン試行回数が多すぎます。しばらくしてからお試しください", "error")
            return render_template("login.html", next_url=next_url), 429

        user = User.query.filter_by(username=username).first()  # ユーザーをクエリ
        # ユーザーの有無で処理時間が変わらないよう、常にハッシュ検証を行う
        hash_to_check = user.password if user else _DUMMY_HASH
        password_ok = check_password_hash(hash_to_check, password)
        if user and password_ok:  # ユーザーが存在し、パスワードが一致する場合
            _login_failures.pop(key, None)
            session.clear()  # セッション固定化対策
            session.permanent = True  # PERMANENT_SESSION_LIFETIME(7日)を有効にする
            login_user(user)  # ログイン
            return redirect(next_url or url_for("home"))  # 元のページ or ホームへ
        else:  # 一致しない場合
            _login_failures[key].append(time.time())
            flash("ユーザー名またはパスワードが違います", "error")  # エラーフラッシュ
    return render_template(
        "login.html", next_url=next_url
    )  # ログインテンプレートをレンダリング


@app.route("/logout", methods=["POST"])  # POST限定（GETだと外部リンク等で勝手にログアウトされる）
@login_required  # ログイン必須
def logout():  # ログアウト関数
    logout_user()  # ログアウト
    return redirect(url_for("index"))  # インデックスへリダイレクト


if __name__ == "__main__":  # スクリプトが直接実行された場合
    with app.app_context():  # Flaskアプリコンテキスト内で
        db.create_all()  # テーブルを作成
    # デフォルトではデバッグを無効化。開発時に明示的に有効化するには
    # 環境変数 FLASK_DEBUG=1 を設定してください。
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    port = int(os.getenv("PORT", 5000))
    # デバッガ露出を避けるため、既定ではローカルホストのみで待ち受ける
    host = os.getenv("HOST", "127.0.0.1")
    app.run(debug=debug_mode, host=host, port=port)
