from flask_sqlalchemy import SQLAlchemy  # データベース操作を可能にする
from flask_login import UserMixin  # ユーザー認証機能を追加
from datetime import datetime  # 日時操作
import json  # JSONデータの操作

db = SQLAlchemy()  # SQLAlchemyのインスタンスを作成してデータベース接続を管理


# ------------------------------
# ユーザー管理
# ------------------------------
class User(UserMixin, db.Model):  # UserMixinを継承したUserモデルクラスを定義（Flask-Login用）
    __tablename__ = "users"  # データベースのテーブル名を"users"に設定

    id = db.Column(db.Integer, primary_key=True)  # 主キーとなるidカラム（整数型）
    username = db.Column(db.String(150), unique=True, nullable=False)  # ユーザー名カラム（文字列、ユニーク、必須）
    password = db.Column(db.String(150), nullable=False)  # パスワードカラム（文字列、必須）
    created_at = db.Column(db.DateTime, default=datetime.now)  # 作成日時カラム（デフォルトは現在時刻）

    # リレーション: 履歴
    logs = db.relationship(  # LearningLogとのリレーションを設定
        "LearningLog", backref="user", lazy=True, cascade="all, delete-orphan"  # 逆参照を"user"とし、遅延ロード、削除時は関連データを削除
    )


# ------------------------------
# 問題管理 (JSON同期用)
# ------------------------------
class Problem(db.Model):  # Problemモデルクラスを定義
    __tablename__ = "problems"  # データベースのテーブル名を"problems"に設定

    # 検索・一覧表示に必要なメタデータはカラムにする
    id = db.Column(db.String(50), primary_key=True)  # 主キーとなるidカラム（文字列、JSONの"id"と一致）
    mode = db.Column(db.String(20), nullable=False)  # モードカラム（'practice' or 'exam'、必須）
    title = db.Column(db.String(200), nullable=False)  # タイトルカラム（文字列、必須）
    category = db.Column(db.String(50))  # カテゴリカラム（文字列、オプション）
    difficulty = db.Column(db.String(20))  # 難易度カラム（文字列、オプション）

    # 問題の中身
    description = db.Column(db.Text)  # 問題文カラム（簡易表示用）
    code_text = db.Column(db.Text, nullable=False)  # 表示用コードカラム（テキスト、必須）
    variables_str = db.Column(db.String(200))  # 変数リストカラム（カンマ区切り文字列）

    # 過去問演習モードの問題を保存するためのJSONカラム
    content_json = db.Column(db.Text, default="[]", nullable=True)

    # 複雑なデータはJSON文字列として保存
    data_json = db.Column(db.Text, nullable=False)  # 複雑なデータ（steps/options）をJSON文字列として保存（必須）

    # テンプレートで使いやすくするためのヘルパープロパティ
    @property  # プロパティデコレータ：data_jsonを辞書型で返す
    def data(self):  # data_jsonをJSONから辞書に変換して返すメソッド
        """data_json を辞書型に戻して返す"""
        try:  # JSONパースを試行
            return json.loads(self.data_json)  # JSON文字列を辞書に変換
        except json.JSONDecodeError:  # パース失敗時
            return {}  # 空の辞書を返す

    @property  # プロパティデコレータ：variables_strをリストで返す
    def variables(self):  # variables_strをリストに変換して返すメソッド
        """variables_str をリストに戻して返す"""
        if self.variables_str:  # variables_strが存在する場合
            return self.variables_str.split(",")  # カンマで分割してリスト化
        return []  # 存在しない場合は空リスト
    
    @property
    def content(self):
        """content_json をリストに戻して返す"""
        if not self.content_json:
            return []
        try:
            items = json.loads(self.content_json)

            # テンプレートに渡す前に画像パスを検証する
            for item in items:
                # "src"キーを持つ要素をチェック
                if item.get("src"):
                    src = item["src"]
                    # 1. ".." (親ディレクトリへの移動) を禁止
                    # 2. "images/" で始まること (静的ファイルの所定フォルダに限定)
                    if ".." in src or not src.startswith("images/"):
                        # 不正なパス検知時は、安全なダミー画像や空文字に置換して無効化
                        # (必要に応じて 'images/error.png' などを用意してください)
                        item["src"] = ""
        except json.JSONDecodeError:
            return []

    def to_dict(self):  # フロントエンド用にデータを辞書化するメソッド
        """フロントエンド(JS)用にデータを辞書化するメソッド"""
        # 基本データの辞書を作成
        data = {  # 基本情報を辞書にまとめる
            "id": self.id,  # idを追加
            "title": self.title,  # タイトルを追加
            "description": self.description,  # 説明を追加
            "category": self.category,  # カテゴリを追加
            "difficulty": self.difficulty,  # 難易度を追加
            "variables": self.variables,  # 変数リストを追加（プロパティ経由）
            "code_template": self.code_text,  # コードテンプレートを追加（過去問モード用）
            "code": self.code_text.split("\n"),  # コードを行ごとの配列に分割（練習モード用）
            "content": self.content
        }

        # JSONカラムの中身（steps や options）をマージする
        # self.data は @property で定義した辞書返却メソッド
        data.update(self.data)  # dataプロパティの内容をマージ

        return data  # 辞書を返す

    # リレーション
    logs = db.relationship("LearningLog", backref="problem", lazy=True)  # LearningLogとのリレーションを設定


# ------------------------------
# 学習履歴
# ------------------------------
class LearningLog(db.Model):  # LearningLogモデルクラスを定義
    __tablename__ = "learning_logs"  # データベースのテーブル名を"learning_logs"に設定

    id = db.Column(db.Integer, primary_key=True)  # 主キーとなるidカラム（整数型）
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)  # 外部キー：usersテーブルのid（必須）
    problem_id = db.Column(db.String(50), db.ForeignKey("problems.id"), nullable=False)  # 外部キー：problemsテーブルのid（必須）
    mode = db.Column(db.String(20), nullable=False)  # モードカラム（必須）
    is_correct = db.Column(db.Boolean, default=False)  # 正解フラグカラム（デフォルトFalse）
    timestamp = db.Column(db.DateTime, default=datetime.now)  # タイムスタンプカラム（デフォルト現在時刻）
