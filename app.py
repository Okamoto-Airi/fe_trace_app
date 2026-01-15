import os  # OS関連の操作を行うためのモジュールをインポート
import json  # JSONデータの操作を行うためのモジュールをインポート
import click  # Flask CLIコマンドを作成するためのモジュールをインポート

from flask import (
    Flask,
    render_template,
    request,
    redirect,
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
from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)  # パスワードハッシュ化と検証関数をインポート
from datetime import datetime, timedelta  # 日時操作のためのモジュールをインポート
from dotenv import load_dotenv  # 環境変数をロードするためのモジュールをインポート

# models.py からインポート
from models import db, User, Problem, LearningLog  # データベースモデルをインポート


# .envファイルをロード (最初に行う)
load_dotenv()

app = Flask(__name__)  # Flaskアプリケーションインスタンスを作成
# 設定 (環境変数がなければデフォルト値を使用)
app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY", "dev-key-default"
)  # 秘密鍵を設定（環境変数から取得、なければデフォルト）
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.getenv(  # データベースURIを設定（環境変数から取得、なければSQLiteデフォルト）
        "DATABASE_URL", "sqlite:///database.db"
    )
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # SQLAlchemyの変更追跡を無効化

db.init_app(app)  # SQLAlchemyをFlaskアプリに初期化
login_manager = LoginManager()  # LoginManagerインスタンスを作成
login_manager.init_app(app)  # LoginManagerをFlaskアプリに初期化
login_manager.login_view = (
    "login"  # ログインが必要なページでリダイレクトするビューを設定
)


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
        problem.data_json = json.dumps(p_data.get("data", {}), ensure_ascii=False)

        if "content" in p_data:
            # 1. content をそのまま保存
            problem.content_json = json.dumps(p_data["content"], ensure_ascii=False)

            # 2. code_text カラムへのバックフィル (DBのNot Null制約回避 & 検索用)
            # content 内の type="code" のブロックを探す
            code_block = next(
                (item for item in p_data["content"] if item.get("type") == "code"), None
            )
            if code_block:
                problem.code_text = code_block["text"]  # 新JSONのキーは "text"
            else:
                # コードがない問題は稀だが、空文字を入れておく
                problem.code_text = ""

            # 3. description カラムへのバックフィル
            # 最初のテキストブロックを説明文として保存しておく
            text_block = next(
                (item for item in p_data["content"] if item.get("type") == "text"), None
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


# --- ルーティング ---


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
    # --- 学習ログ取得 ---
    logs = (
        LearningLog.query.filter_by(user_id=current_user.id)
        .order_by(LearningLog.timestamp.desc())
        .all()
    )

    # --- 連続学習日数計算 ---
    studied_dates = {log.timestamp.date() for log in logs}
    today = datetime.now().date()
    streak = 0

    check_date = today
    if today not in studied_dates:
        check_date = today - timedelta(days=1)

    while check_date in studied_dates:
        streak += 1
        check_date -= timedelta(days=1)

    # --- 称号計算 ---
    solved_count = sum(1 for log in logs if log.is_correct)
    # solved_count = 50  # ←ホームのテスト用。

    if solved_count >= 30:
        rank = "トレースレジェンド"
        rank_color = "text-yellow-500"
    elif solved_count >= 25:
        rank = "トレースマスター"
        rank_color = "text-purple-500"
    elif solved_count >= 20:
        rank = "トレース職人"
        rank_color = "text-red-500"
    elif solved_count >= 10:
        rank = "トレース上級者"
        rank_color = "text-sky-500"
    else:
        rank = "トレース見習い"
        rank_color = "text-lime-600"

    # グラフ用データの集計 (過去7日間)
    graph_labels = []  # 日付（例："1/5"）
    data_practice = []  # 練習モードの正解数
    data_exam = []  # 過去問モードの正解数

    for i in range(6, -1, -1):  # 6日前〜今日
        target_date = today - timedelta(days=i)
        # ラベル作成（月/日）
        graph_labels.append(target_date.strftime("%m/%d"))

        # モード別に集計
        count_practice = sum(
            1
            for log in logs
            if log.timestamp.date() == target_date
            and log.is_correct
            and log.mode == "practice"
        )
        count_exam = sum(
            1
            for log in logs
            if log.timestamp.date() == target_date
            and log.is_correct
            and log.mode == "exam"
        )

        data_practice.append(count_practice)
        data_exam.append(count_exam)

    # --- テンプレートへ返す ---
    return render_template(
        "home.html",
        streak=streak,
        rank=rank,
        rank_color=rank_color,
        recent_logs=logs[:5],
        graph_labels=graph_labels,
        data_practice=data_practice,
        data_exam=data_exam
    )  # ホームテンプレートをレンダリング


@app.route("/problems/<mode>")  # /problems/<mode> URLのルーティング
@login_required  # ログイン必須
def problem_list(mode):  # 問題リスト関数
    # 辞書ではなくDBからフィルタリングして取得
    if mode not in ["practice", "exam"]:  # モードが有効でない場合
        return redirect(url_for("home"))  # ホームへリダイレクト

    problems = Problem.query.filter_by(mode=mode).all()  # 指定モードの問題をクエリ

    # 称号計算用 (ヘッダー表示用)
    solved_count = LearningLog.query.filter_by(  # 正解したログをカウント
        user_id=current_user.id, is_correct=True
    ).count()

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
    # (models.py で定義した @property def data により、テンプレート側で problem.data.steps のようにアクセス可能)
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
    solved_count = LearningLog.query.filter_by(
        user_id=current_user.id, is_correct=True
    ).count()

    # solved_count = 50   # ←プロフィールのテスト用。

    # --- 称号計算（home と同じロジック） ---
    if solved_count >= 30:
        rank = "トレースレジェンド"
        rank_color = "text-yellow-500"
    elif solved_count >= 25:
        rank = "トレースマスター"
        rank_color = "text-purple-500"
    elif solved_count >= 20:
        rank = "トレース職人"
        rank_color = "text-red-500"
    elif solved_count >= 10:
        rank = "トレース上級者"
        rank_color = "text-sky-500"
    else:
        rank = "トレース見習い"
        rank_color = "text-lime-600"

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
    data = request.json  # JSONデータを取得
    new_log = LearningLog(  # 新しいLearningLogインスタンスを作成
        user_id=current_user.id,  # ユーザーID
        problem_id=data.get("problem_id"),  # 問題ID
        mode=data.get("mode", "practice"),  # モード（デフォルトpractice）
        is_correct=data.get("is_correct", False),  # 正解フラグ（デフォルトFalse）
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
    if request.method == "POST":  # POSTリクエストの場合
        username = request.form["username"]  # ユーザー名を取得
        password = request.form["password"]  # パスワードを取得
        if User.query.filter_by(
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
            db.session.commit()  # コミット
            login_user(new_user)  # ログイン
            return redirect(url_for("home"))  # ホームへリダイレクト
    return render_template("register.html")  # 登録テンプレートをレンダリング


@app.route(
    "/login", methods=["GET", "POST"]
)  # /login URLのルーティング（GET/POSTメソッド）
def login():  # ログイン関数
    if request.method == "POST":  # POSTリクエストの場合
        username = request.form["username"]  # ユーザー名を取得
        password = request.form["password"]  # パスワードを取得
        user = User.query.filter_by(username=username).first()  # ユーザーをクエリ
        if user and check_password_hash(
            user.password, password
        ):  # ユーザーが存在し、パスワードが一致する場合
            login_user(user)  # ログイン
            return redirect(url_for("home"))  # ホームへリダイレクト
        else:  # 一致しない場合
            flash("ユーザー名またはパスワードが違います", "error")  # エラーフラッシュ
    return render_template("login.html")  # ログインテンプレートをレンダリング


@app.route("/logout")  # /logout URLのルーティング
@login_required  # ログイン必須
def logout():  # ログアウト関数
    logout_user()  # ログアウト
    return redirect(url_for("index"))  # インデックスへリダイレクト


if __name__ == "__main__":  # スクリプトが直接実行された場合
    with app.app_context():  # Flaskアプリコンテキスト内で
        db.create_all()  # テーブルを作成
    app.run(debug=True, port=5000)  # デバッグモードでアプリを実行（ポート5000）
