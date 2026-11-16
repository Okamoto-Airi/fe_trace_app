# FE TraceMaster (基本情報技術者試験 トレース学習アプリ)

## 概要

基本情報技術者試験 科目Bのトレース問題を練習・演習できるWebアプリです。

## 開発環境のセットアップ手順

1. リポジトリのクローン

   ```bash
   git clone <リポジトリURL>
   cd fe_trace_app
   ```

2. 仮想環境の作成と有効化

    ```bash
    # Windows
    python -m venv venv
    .\venv\Scripts\activate

    # Mac/Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

3. ライブラリのインストール

    ```bash
    pip install -r requirements.txt
    ```

4. 環境変数の設定

    `.env.example`をコピーして`.env`を作成してください。 (必要に応じてSECRET_KEYなどを書き換えてください)

5. データベースの初期化

    ```bash
    flask init-db   # テーブル作成
    flask seed-db   # データ投入
    ```

6. アプリの起動

    ```Bash
    python app.py
    ```

    <http://127.0.0.1:5000> にアクセス


## CSS（Tailwind）の更新

画面のスタイルは Tailwind CSS で作り、ビルド済みの `static/css/tailwind.css` をリポジトリに含めています
（本番サーバーに Node.js は不要です）。

テンプレート（`templates/*.html`）や `app.py` に**新しい Tailwind のクラスを追加・変更したとき**だけ、再ビルドしてコミットしてください。

```bash
npm install        # 初回のみ
npm run build:css  # static/css/tailwind.css を再生成
```

> **注意:** プロジェクトのパスに日本語などの全角文字が含まれていると、Node.js がクラッシュしてビルドできない場合があります。
> その場合は、英数字だけのパスにプロジェクトをコピーしてビルドし、生成された `static/css/tailwind.css` を戻してください。

## テスト

```bash
python -m unittest discover -s tests -v
```

## 本番デプロイ時の注意

- `SECRET_KEY` は環境変数で必ず設定してください（未設定だと再起動のたびに全員がログアウトされます）。
- CSS を更新したときは `static/css/tailwind.css` も一緒にデプロイされていることを確認してください。
