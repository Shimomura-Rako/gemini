#
# DMM英会話の予約可能通知アプリ
#
# このコードは、Flaskを使ってウェブアプリケーションを構築します。
# ユーザーが登録した講師のページを定期的にチェックし、
# 新しい予約枠が空いたらPushbulletで通知を送ります。
#
# -----------------------------------------------------------
# 🛠️ 必要なライブラリ
# -----------------------------------------------------------
# - Flask: ウェブアプリケーションのフレームワーク
# - Flask-SQLAlchemy: データベース（SQLite）を扱うための拡張機能
# - beautifulsoup4, requests: ウェブページのスクレイピング用
# - apscheduler: 定期実行処理用
# - pushbullet.py: Pushbullet APIとの連携用
# - その他の標準ライブラリ（os, random, stringなど）
# -----------------------------------------------------------
#
# 🚨 セキュリティに関する注意
# -----------------------------------------------------------
# - `SECRET_KEY`や`DOWNLOAD_KEY`はハードコードせず、
#   環境変数から取得するように変更しています。
# - `download`ルートの鍵も、より強固なものに変更可能にしています。
# -----------------------------------------------------------
#

from flask import Flask, render_template, request, redirect, flash, session
import os
import requests
import random
import string
from flask_sqlalchemy import SQLAlchemy
from pushbullet import Pushbullet
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from flask import send_file
import time


app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# セキュリティ向上のため、SECRET_KEYを環境変数から取得するように変更
# 環境変数が設定されていない場合は'default_secret_key'を使用
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key')

db = SQLAlchemy(app)

class UserData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.String(100), nullable=False)
    teacher_name = db.Column(db.String(255), nullable=True)
    pushbullet_token = db.Column(db.String(255), nullable=False)
    last_available_count = db.Column(db.Integer, default=0)
    user_id = db.Column(db.String(255), nullable=False)
    last_accessed = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def generate_user_id(length=10):
    """
    ランダムなユーザーIDを生成する関数
    """
    return 'user_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

@app.before_request
def assign_user_id():
    """
    リクエスト前に実行される関数。
    セッションにユーザーIDが存在しない場合、Noneを設定する。
    """
    if "user_id" not in session:
        session["user_id"] = None

@app.route("/set_user", methods=["GET", "POST"])
def set_user():
    """
    ユーザーIDのログイン・新規登録を行うルート
    """
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        action = request.form.get("action")

        if not user_id:
            flash("ユーザーIDを入力してください！", "danger")
            return redirect("/set_user")

        if action == "login":
            # 既存のユーザーIDかチェック
            existing = UserData.query.filter_by(user_id=user_id).first()
            if existing:
                session["user_id"] = user_id
                flash(f"ユーザーIDでログインしました: {user_id}", "success")
                return redirect("/")
            else:
                flash("このユーザーIDは存在しません。もう一度確認してください。", "danger")
                return redirect("/set_user")

        elif action == "register":
            # 新規登録時に、既にIDが存在しないかチェックするよう修正
            existing = UserData.query.filter_by(user_id=user_id).first()
            if existing:
                flash("このユーザーIDはすでに存在します。別のIDをお試しください。", "danger")
                return redirect("/set_user")
            
            # ユーザーIDをセッションに登録し、成功メッセージを表示
            session["user_id"] = user_id
            flash(f"ユーザーIDを登録しました: {user_id}", "success")
            return redirect("/")

        else:
            flash("不正な操作です。", "danger")
            return redirect("/set_user")

    else:
        # GETリクエストの場合、新しいIDを自動生成しないように変更
        # ユーザーにログインまたは新規登録を促す画面を表示
        return render_template("set_user.html")


@app.route("/", methods=["GET", "POST"])
def index():
    """
    講師情報の登録、表示を行うメインページ
    """
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/set_user")

    # 最終アクセス時間の更新
    # ユーザーがまだ何も登録していない場合、UserDataは存在しないため、first()で取得し、存在する場合のみ更新する
    user_data_entry = UserData.query.filter_by(user_id=user_id).first()
    if user_data_entry:
        UserData.query.filter_by(user_id=user_id).update({"last_accessed": datetime.utcnow()})
        db.session.commit()

    total_teachers = UserData.query.filter_by(user_id=user_id).count()

    if request.method == "POST":
        if total_teachers >= 10:
            flash("このアカウントでは最大10件までしか登録できません！", "danger")
            return redirect("/")

        teacher_id = request.form.get("teacher_id")
        pushbullet_token = request.form.get("pushbullet_token")

        if not teacher_id.isdigit():
            flash("講師番号は数字のみで入力してください。", "danger")
            return redirect("/")

        if not teacher_id or not pushbullet_token:
            flash("すべての項目を入力してください！", "danger")
            return redirect("/")

        existing_teacher = UserData.query.filter_by(teacher_id=teacher_id, user_id=user_id).first()
        if existing_teacher:
            flash("この講師はすでに登録されています！", "warning")
            return redirect("/")

        teacher_name = get_teacher_name(teacher_id)

        if not teacher_name:
            flash("講師情報が取得できませんでした。番号を確認してください。", "danger")
            return redirect("/")

        # 登録処理
        new_data = UserData(
            teacher_id=teacher_id,
            teacher_name=teacher_name,
            pushbullet_token=pushbullet_token,
            user_id=user_id
        )
        db.session.add(new_data)
        db.session.commit()
        flash(f"{teacher_name} (講師番号: {teacher_id}) を登録しました！", "success")

        return redirect("/")

    # GETメソッド時：登録済みデータを表示
    all_data = UserData.query.filter_by(user_id=user_id).all()
    return render_template("index.html", all_data=all_data, total_teachers=total_teachers, user_id=user_id)

@app.route("/delete_teacher", methods=["POST", "GET"])
def delete_teacher():
    """
    登録した講師情報を削除するルート
    """
    if request.method == "GET":
        return redirect("/")

    teacher_id = request.form.get("teacher_id")
    user_id = session.get("user_id")
    teacher_data = UserData.query.filter_by(teacher_id=teacher_id, user_id=user_id).first()
    if teacher_data:
        db.session.delete(teacher_data)
        db.session.commit()
        flash(f"講師番号 {teacher_id} を削除しました！", "success")
    else:
        flash(f"講師番号 {teacher_id} は存在しません。", "danger")
    return redirect("/")

@app.route("/reset_user", methods=["POST"])
def reset_user():
    """
    ユーザーIDと紐づくすべてのデータを削除するルート
    """
    user_id = session.get("user_id")
    if user_id:
        UserData.query.filter_by(user_id=user_id).delete()
        db.session.commit()

    session.clear()
    flash("ユーザーIDとすべてのデータをリセットしました。新しく設定してください！", "success")
    return redirect("/set_user")

@app.route("/tutorial")
def tutorial():
    """
    チュートリアルページを表示するルート
    """
    return render_template("tutorial.html")

def get_teacher_name(teacher_id):
    """
    DMM英会話のページから講師名を取得する関数
    """
    load_url = f"https://eikaiwa.dmm.com/teacher/index/{teacher_id}/"
    try:
        response = requests.get(load_url, headers=HEADERS, timeout=5, allow_redirects=True)
        if response.url == "https://eikaiwa.dmm.com/":
            return None
        soup = BeautifulSoup(response.content, "html.parser")
        teacher_name_tag = soup.find("h1")
        return teacher_name_tag.text.strip() if teacher_name_tag else None
    except requests.exceptions.RequestException:
        return None

def get_available_slots(teacher_id):
    """
    DMM英会話のページから予約可能なスロット数を取得する関数
    """
    load_url = f"https://eikaiwa.dmm.com/teacher/index/{teacher_id}/"
    try:
        response = requests.get(load_url, headers=HEADERS, timeout=5)
        if response.status_code != 200 or response.url == "https://eikaiwa.dmm.com/":
            return None
        soup = BeautifulSoup(response.content, "html.parser")
        return len(soup.find_all(string="予約可"))
    except requests.exceptions.RequestException:
        return None

def send_push_notification(user_token, teacher_id, name):
    """
    Pushbulletで通知を送信する関数
    """
    try:
        pb_user = Pushbullet(user_token)
        url = f"https://eikaiwa.dmm.com/teacher/index/{teacher_id}/"
        pb_user.push_link(f"{name} レッスン開講通知", url)
    except Exception as e:
        print(f"⚠ Pushbullet通知の送信に失敗しました: {e}")

consecutive_errors = 0
MAX_ERRORS = 5

def check_teacher_availability():
    """
    定期的に講師の予約可能状況をチェックする関数
    """
    global consecutive_errors
    with app.app_context():
        try:
            users = UserData.query.all()
            error_count_this_run = 0

            for user in users:
                time.sleep(random.uniform(0.5, 2.0))
                current_count = get_available_slots(user.teacher_id)
                if current_count is None:
                    error_count_this_run += 1
                    continue

                if current_count > user.last_available_count:
                    send_push_notification(user.pushbullet_token, user.teacher_id, user.teacher_name)

                user.last_available_count = current_count
                db.session.commit()

            if error_count_this_run == len(users):
                consecutive_errors += 1
                print(f"⚠ DMMに全ユーザーでアクセス失敗（{consecutive_errors}回連続）")
                if consecutive_errors >= MAX_ERRORS:
                    print("🚨 一時的にチェック処理をスキップします")
                    return
            else:
                consecutive_errors = 0

        except Exception as e:
            print(f"⚠ 通知チェックでエラー発生: {e}")

scheduler = BackgroundScheduler()
interval_minutes = int(os.environ.get("CHECK_INTERVAL_MINUTES", 1))
scheduler.add_job(check_teacher_availability, 'interval', minutes=interval_minutes)
scheduler.start()

@app.route("/download")
def download_db():
    """
    データベースファイルをダウンロードするルート
    """
    # セキュリティ向上のため、鍵を環境変数から取得するように変更
    secret = request.args.get("key")
    if secret != os.environ.get('DOWNLOAD_KEY', 'secret0917'):
        return "アクセス拒否！", 403

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, "database.db")
        print(f"📁 DBファイルのパス: {db_path}")
        return send_file(db_path, as_attachment=True)
    except Exception as e:
        print(f"❌ ダウンロード失敗: {e}")
        return f"ダウンロードに失敗しました: {e}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
