# -*- coding: utf-8 -*-
"""
سهلتهالك بكالوريا — تطبيق Flask في ملف واحد
التثبيت:  py -m pip install flask
التشغيل:   py app.py
افتح:      http://127.0.0.1:5000

لوحة الإدارة: http://127.0.0.1:5000/admin/login
اسم الدخول الافتراضي: admin
كلمة المرور الافتراضية: ChangeMe123!
غيّر كلمة المرور بتعيين متغير البيئة ADMIN_PASSWORD قبل التشغيل.
رقم المحفظة المعروض للطلاب: 01279790505

ملاحظة: هذا تطبيق بداية محلي. لا توجد بوابة دفع حقيقية.
لا ترفعه للإنترنت كما هو قبل إعداد HTTPS، ونسخ احتياطي، وتغيير كلمات المرور.
"""
import os
import re
import sqlite3
import secrets
import random
from functools import wraps
from pathlib import Path
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import (
    Flask, g, request, redirect, url_for, session, flash,
    render_template_string, abort, send_from_directory, send_file
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "sahelha.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
WALLET_NUMBER = "01279790505"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        wallet INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        subject TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        price INTEGER NOT NULL DEFAULT 0,
        cover_emoji TEXT NOT NULL DEFAULT '📚',
        lessons TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS enrollments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, course_id)
    );
    CREATE TABLE IF NOT EXISTS topups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount INTEGER NOT NULL,
        sender_phone TEXT NOT NULL,
        receipt_file TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        admin_note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        reviewed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        a TEXT NOT NULL,
        b TEXT NOT NULL,
        c TEXT NOT NULL,
        d TEXT NOT NULL,
        correct INTEGER NOT NULL CHECK(correct BETWEEN 0 AND 3),
        explanation TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    # Safe additive migration for databases created by earlier versions.
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "disabled" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
    # Initial sample courses, inserted only on first run.
    count = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    if count == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        samples = [
            ("اللغة العربية", "اللغة العربية", "شرح مبسط للنحو والبلاغة والأدب مع تدريبات على المنهج.", 180, "📚", "مدخل إلى المنهج\nالنحو والتطبيقات\nالبلاغة\nالأدب والنصوص"),
            ("اللغة الإنجليزية", "اللغة الإنجليزية", "قواعد ومفردات وتدريبات تساعدك على فهم الأسئلة بثقة.", 160, "🌐", "Grammar basics\nVocabulary\nReading skills\nPractice test"),
            ("التاريخ", "التاريخ", "شرح الأحداث التاريخية بطريقة منظمة مع مراجعة شاملة.", 140, "🏛️", "مقدمة تاريخية\nالأحداث الرئيسية\nتحليل المصادر\nمراجعة نهائية"),
        ]
        conn.executemany("""INSERT INTO courses
            (title,subject,description,price,cover_emoji,lessons,active,created_at)
            VALUES (?,?,?,?,?,?,1,?)""", [x + (now,) for x in samples])
    # Create one initial admin account in the database if missing.
    admin = conn.execute("SELECT id FROM users WHERE phone = ?", ("admin",)).fetchone()
    if not admin:
        conn.execute("""INSERT INTO users
            (name,phone,password_hash,wallet,is_admin,created_at)
            VALUES (?,?,?,?,1,?)""",
            ("مدير المنصة", "admin", generate_password_hash(ADMIN_PASSWORD), 0,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


@app.context_processor
def inject_globals():
    return {"me": current_user(), "wallet_number": WALLET_NUMBER}


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("سجّل الدخول الأول علشان تكمل.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or not u["is_admin"]:
            flash("الصفحة دي للإدارة فقط.", "warning")
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


@app.context_processor
def inject_csrf():
    return {"csrf_token": csrf_token}


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        expected = session.get("_csrf", "")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            abort(400, "انتهت صلاحية النموذج. ارجع للصفحة وحاول مرة أخرى.")


BASE = r"""
<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title or 'سهلتهالك بكالوريا' }}</title>
<style>
:root{--bg:#f3f6fb;--card:#fff;--ink:#142b45;--muted:#718198;--teal:#087f8c;--line:#dce5ef;--soft:#e8f5f7;--navy:#102a43;--shadow:0 8px 24px rgba(24,55,85,.07)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at top right,#e8f4fa 0,transparent 30%),var(--bg);color:var(--ink);font:15px Tahoma,Arial,sans-serif}
a{color:var(--teal);text-decoration:none}button,input,textarea,select{font:inherit}
.top{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.96);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:13px max(18px,calc((100% - 1220px)/2));display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;box-shadow:0 3px 18px rgba(20,43,69,.04)}
.brand{font-size:21px;font-weight:900;color:var(--ink);white-space:nowrap;line-height:1.4}.brand small{display:block;font-size:10px;letter-spacing:1.5px;color:var(--teal);margin-top:2px}
nav{display:flex;gap:8px;flex-wrap:wrap;align-items:center}nav a{display:inline-flex;align-items:center;justify-content:center;padding:10px 13px;border:1px solid var(--line);border-radius:12px;background:#fff;color:#50647a;font-size:13px;font-weight:700;box-shadow:0 2px 6px rgba(24,55,85,.025);transition:transform .18s,background .18s,border-color .18s}nav a:hover{background:var(--soft);border-color:#b8dfe3;color:var(--teal);transform:translateY(-1px)}
.wrap{max-width:1220px;margin:26px auto;padding:0 18px;min-height:62vh}.hero{position:relative;overflow:hidden;background:linear-gradient(125deg,#fff 5%,#e6f7f7 100%);border:1px solid #cfe8eb;border-radius:24px;padding:clamp(22px,4vw,42px);margin-bottom:26px;box-shadow:var(--shadow)}.hero:after{content:'✦';position:absolute;left:28px;bottom:-35px;font-size:145px;color:rgba(8,127,140,.06);pointer-events:none}
h1{font-size:clamp(26px,4vw,40px);line-height:1.5;margin:8px 0}h2{font-size:22px;margin:0 0 16px}h3{font-size:17px;margin:0 0 9px}p{line-height:1.9;color:var(--muted)}.muted{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:17px}.card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:var(--shadow);transition:transform .18s,border-color .18s,box-shadow .18s}.grid .card:hover{transform:translateY(-3px);border-color:#b7dce1;box-shadow:0 13px 28px rgba(24,55,85,.09)}.cover{background:linear-gradient(135deg,#d9f5f0,#edf4ff);border:1px solid #e0eeee;border-radius:14px;padding:18px;font-size:38px;margin-bottom:15px;text-align:center}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid var(--line);border-radius:11px;padding:11px 15px;background:white;color:var(--ink);cursor:pointer;font-weight:700;transition:transform .15s,filter .15s;max-width:100%}.btn:hover{filter:brightness(.98);transform:translateY(-1px)}.primary{background:linear-gradient(135deg,#078c91,#087f8c);border-color:#087f8c;color:white;font-weight:bold;box-shadow:0 4px 10px rgba(8,127,140,.15)}
.field{display:block;width:100%;padding:12px 13px;border:1px solid #d4e0ea;border-radius:11px;background:#fff;color:var(--ink);margin:8px 0 16px;outline:none;transition:border-color .15s,box-shadow .15s}.field:focus{border-color:#58b6bd;box-shadow:0 0 0 3px rgba(8,127,140,.10)}textarea.field{min-height:110px;resize:vertical}label{display:block;font-size:13px;color:#526a7e;font-weight:700;margin-top:5px}.row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.notice{padding:14px 16px;border-radius:13px;background:#fff8e8;border:1px solid #f0dfb6;color:#795b22;font-size:13px;line-height:1.9;margin:14px 0}.flash{padding:13px 16px;background:#e8f8f6;border:1px solid #bce9e2;border-radius:12px;margin-bottom:14px;line-height:1.8}.flash.warning{background:#fff8e8;border-color:#f0dfb6}
.table-wrap{width:100%;overflow-x:auto;border:1px solid var(--line);border-radius:14px;margin:14px 0;background:white;box-shadow:var(--shadow)}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:right;padding:13px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top;line-height:1.8}th{background:#edf6f8;color:#29465e;white-space:nowrap}tr:last-child td{border-bottom:0}tr:hover td{background:#fbfdff}
.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.status{display:inline-block;border-radius:8px;padding:6px 9px;font-size:11px;font-weight:700;background:#edf4f7}.pending{background:#fff3d7;color:#85621a}.approved{background:#e2f7eb;color:#207747}.rejected{background:#ffeded;color:#9b3838}
form{max-width:100%}form button{margin-top:5px}.footer{text-align:center;color:#8597a5;padding:25px 18px;font-size:11px;border-top:1px solid var(--line);background:#fff;margin-top:30px}
@media(max-width:900px){.top{align-items:flex-start}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}nav{flex:1}nav a{padding:9px 11px}}
@media(max-width:600px){.top{position:static;padding:13px;gap:12px}.brand{font-size:19px}nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;gap:7px}nav a{padding:10px 7px;text-align:center;font-size:12px;border-radius:10px}.wrap{margin:16px auto;padding:0 12px}.grid{grid-template-columns:1fr;gap:12px}.row{grid-template-columns:1fr;gap:0}.hero{padding:21px;border-radius:18px}.card{padding:16px}h2{font-size:20px}.actions .btn{font-size:12px;padding:9px 11px}th,td{padding:10px;font-size:12px}}
</style></head><body>
<header class="top"><a class="brand" href="{{ url_for('home') }}">◈ سهلتهالك<small>بكالوريا · BAC</small></a>
<nav><a href="{{ url_for('home') }}">الرئيسية</a><a href="{{ url_for('courses') }}">الكورسات</a>
{% if me %}<a href="{{ url_for('my_courses') }}">كورساتي</a><a href="{{ url_for('exams') }}">الامتحانات</a><a href="{{ url_for('wallet') }}">المحفظة</a><a href="{{ url_for('forum') }}">مجتمع الطلاب</a><a href="{{ url_for('my_progress') }}">نتائجي</a>{% endif %}
{% if me and me['is_admin'] %}<a href="{{ url_for('admin') }}">الإدارة</a>{% endif %}
{% if me %}<a href="{{ url_for('logout') }}">خروج</a>{% else %}<a href="{{ url_for('login') }}">دخول</a><a href="{{ url_for('register') }}">حساب جديد</a>{% endif %}</nav></header>
<main class="wrap">
{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% endwith %}
{{ body|safe }}
</main><footer class="footer">سهلتهالك بكالوريا © {{ year }} · مدفوعات تجريبية فقط حتى ربط بوابة دفع حقيقية</footer>
</body></html>
"""

def page(body, title="سهلتهالك بكالوريا"):
    return render_template_string(BASE, body=body, title=title, year=datetime.now().year)


def form_token():
    return '<input type="hidden" name="_csrf" value="{{ csrf_token() }}">'

@app.route("/")
def home():
    courses = db().execute("SELECT * FROM courses WHERE active=1 ORDER BY id DESC").fetchall()
    body = """
    <section class="hero"><div style="color:#078f8d;font-size:12px">✦ رحلتك نحو التفوق تبدأ هنا</div>
    <h1>مستقبلك بتصنعه،<br><span style="color:#078f8d">خطوة بخطوة.</span></h1>
    <p>كورسات واختبارات ومجتمع طلابي في مكان واحد.</p>
    <a class="btn primary" href="{{ url_for('courses') }}">اكتشف الكورسات ←</a>
    {% if me and not me['is_admin'] %}<a class="btn" href="{{ url_for('wallet') }}">رصيدك: {{ me['wallet'] }} ج.م</a>{% endif %}
    </section><div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap"><h2>الكورسات المتاحة</h2><a href="{{ url_for('courses') }}">كل الكورسات ←</a></div>
    <div class="grid">{% for c in courses %}<article class="card"><div class="cover">{{ c['cover_emoji'] }}</div><h3>{{ c['title'] }}</h3><p>{{ c['description'] }}</p><strong style="color:#078f8d">{{ c['price'] }} ج.م</strong><p><a class="btn primary" href="{{ url_for('course_detail',course_id=c['id']) }}">تفاصيل الكورس</a></p></article>{% else %}<p>لم تُضف كورسات بعد.</p>{% endfor %}</div>
    """
    return page(render_template_string(body, courses=courses), "الرئيسية")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        if not name or not re.fullmatch(r"[0-9+\-\s]{8,20}", phone) or len(password) < 8:
            flash("اكتب اسمًا ورقم هاتف صحيحًا وكلمة مرور من 8 أحرف على الأقل.", "warning")
        else:
            try:
                db().execute("INSERT INTO users(name,phone,password_hash,wallet,is_admin,created_at) VALUES(?,?,?,?,0,?)",
                             (name, phone, generate_password_hash(password), 0, now()))
                db().commit()
                user = db().execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
                session.clear()
                session["user_id"] = user["id"]
                flash("تم إنشاء الحساب. أهلًا بيك!", "success")
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                flash("رقم الهاتف مسجل بالفعل.", "warning")
    body = """
    <div class="card" style="max-width:520px;margin:auto"><h2>إنشاء حساب طالب</h2><p>سجّل برقم الهاتف وكلمة مرور خاصة بك.</p>
    <form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
    <label>الاسم</label><input class="field" name="name" maxlength="80" required>
    <label>رقم الهاتف</label><input class="field" name="phone" inputmode="tel" required>
    <label>كلمة المرور (8 أحرف على الأقل)</label><input class="field" type="password" name="password" minlength="8" required>
    <button class="btn primary">إنشاء الحساب</button></form></div>"""
    return page(render_template_string(body), "حساب جديد")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        user = db().execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if user and not user["disabled"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash("تم تسجيل الدخول.", "success")
            return redirect(url_for("admin" if user["is_admin"] else "home"))
        flash("رقم الهاتف أو كلمة المرور غير صحيحة.", "warning")
    body = """
    <div class="card" style="max-width:500px;margin:auto"><h2>تسجيل الدخول</h2>
    <form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
    <label>رقم الهاتف أو اسم الإدارة admin</label><input class="field" name="phone" required>
    <label>كلمة المرور</label><input class="field" type="password" name="password" required>
    <button class="btn primary">دخول</button></form><p><a href="{{ url_for('register') }}">إنشاء حساب جديد</a></p></div>"""
    return page(render_template_string(body), "تسجيل الدخول")

@app.route("/logout")
def logout():
    session.clear()
    flash("تم تسجيل الخروج.", "success")
    return redirect(url_for("home"))

@app.route("/courses")
def courses():
    # فلترة بسيطة حسب المادة من غير تغيير قاعدة البيانات أو بيانات الكورسات.
    subject = request.args.get("subject", "").strip()
    subjects = [r[0] for r in db().execute(
        "SELECT DISTINCT subject FROM courses WHERE active=1 ORDER BY subject"
    ).fetchall()]
    if subject and subject in subjects:
        rows = db().execute(
            "SELECT * FROM courses WHERE active=1 AND subject=? ORDER BY id DESC", (subject,)
        ).fetchall()
    else:
        subject = ""
        rows = db().execute("SELECT * FROM courses WHERE active=1 ORDER BY id DESC").fetchall()
    body = """
    <style>
      .course-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:20px}
      .course-head h1{margin:0 0 4px}.course-count{background:#e4f5f4;color:#087f8c;border:1px solid #c7e9e6;border-radius:12px;padding:9px 13px;font-size:12px;font-weight:800;white-space:nowrap}
      .course-filters{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 24px}
      .filter-chip{display:inline-flex;align-items:center;justify-content:center;padding:9px 14px;border-radius:999px;border:1px solid #dce5ef;background:#fff;color:#50647a;font-size:12px;font-weight:800}
      .filter-chip.active{background:#087f8c;color:#fff;border-color:#087f8c;box-shadow:0 4px 12px #087f8c22}
      .course-card{display:flex;flex-direction:column;gap:0;min-height:100%}.course-card .cover{font-size:43px;padding:22px;margin:0 0 16px;background:linear-gradient(135deg,#e1f8f3,#edf2ff)}
      .course-subject{display:inline-flex;align-self:flex-start;background:#edf6f8;color:#426276;border-radius:8px;padding:5px 9px;font-size:11px;font-weight:800;margin-bottom:10px}
      .course-card h3{font-size:18px;line-height:1.7;margin-bottom:4px}.course-card .course-desc{font-size:13px;line-height:1.9;margin:5px 0 15px;flex:1}
      .course-bottom{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;border-top:1px solid #edf1f5;padding-top:15px;margin-top:auto}
      .course-price small{display:block;font-size:11px;color:#718198;margin-bottom:3px}.course-price strong{font-size:21px;color:#087f8c}.course-card .btn{font-size:12px;padding:10px 12px}
      .empty-courses{grid-column:1/-1;text-align:center;padding:35px}
      @media(max-width:600px){.course-head{align-items:flex-start}.course-bottom{align-items:stretch}.course-card .btn{width:100%}.course-price{margin-bottom:3px}}
    </style>
    <section class="course-head"><div><div style="color:#087f8c;font-size:12px;font-weight:800">✦ تعلّم بطريقتك</div><h1>الكورسات التعليمية</h1><p style="margin:4px 0 0">اختار المادة المناسبة، وشوف محتوى الكورس وسعره قبل الاشتراك.</p></div>
    <div class="course-count">{{ courses|length }} كورس متاح</div></section>
    <div class="course-filters"><a class="filter-chip {% if not selected_subject %}active{% endif %}" href="{{ url_for('courses') }}">كل المواد</a>
    {% for s in subjects %}<a class="filter-chip {% if selected_subject == s %}active{% endif %}" href="{{ url_for('courses',subject=s) }}">{{ s }}</a>{% endfor %}</div>
    <div class="grid">
    {% for c in courses %}<article class="card course-card"><div class="cover">{{ c['cover_emoji'] }}</div><span class="course-subject">{{ c['subject'] }}</span><h3>{{ c['title'] }}</h3><p class="course-desc">{{ c['description'] or 'كورس تعليمي منظم لمساعدتك على المراجعة والاستعداد.' }}</p><div class="course-bottom"><div class="course-price"><small>سعر الكورس</small><strong>{{ c['price'] }} <span style="font-size:12px">ج.م</span></strong></div><a class="btn primary" href="{{ url_for('course_detail',course_id=c['id']) }}">التفاصيل والمحتوى ←</a></div></article>
    {% else %}<div class="card empty-courses"><div style="font-size:38px">📚</div><h3>مفيش كورسات في الاختيار ده حاليًا</h3><p>اختار مادة تانية أو اعرض كل المواد.</p><a class="btn primary" href="{{ url_for('courses') }}">عرض كل الكورسات</a></div>{% endfor %}</div>
    """
    return page(render_template_string(body, courses=rows, subjects=subjects, selected_subject=subject), "الكورسات التعليمية")

@app.route("/course/<int:course_id>")
def course_detail(course_id):
    c = db().execute("SELECT * FROM courses WHERE id=? AND active=1", (course_id,)).fetchone()
    if not c:
        abort(404)
    owned = False
    u = current_user()
    if u:
        owned = bool(db().execute("SELECT id FROM enrollments WHERE user_id=? AND course_id=?", (u["id"], c["id"])).fetchone())
    lessons = [x.strip() for x in c["lessons"].splitlines() if x.strip()]
    exams = db().execute("SELECT * FROM exams WHERE course_id=? AND active=1 ORDER BY id", (c["id"],)).fetchall()
    exam_cards = []
    if u and not u["is_admin"]:
        for exam in exams:
            unlocked, completed, previous_title = exam_access_state(u["id"], exam)
            exam_cards.append({"exam": exam, "unlocked": unlocked, "completed": completed,
                               "previous_title": previous_title})
    else:
        for exam in exams:
            exam_cards.append({"exam": exam, "unlocked": bool(u and u["is_admin"]),
                               "completed": False, "previous_title": None})
    body = """
    <div class="card"><div class="cover">{{ c['cover_emoji'] }}</div><h1>{{ c['title'] }}</h1><p>{{ c['description'] }}</p>
    <h3>محتوى الكورس</h3>{% for lesson in lessons %}<div style="padding:11px;border-bottom:1px solid #e5edf1">▶ {{ lesson }}</div>{% else %}<p>الإدارة لم تضف دروسًا بعد.</p>{% endfor %}
    <p><strong style="color:#078f8d;font-size:21px">{{ c['price'] }} ج.م</strong></p>
    {% if owned %}<div class="flash">الكورس مفعّل على حسابك.</div>{% elif me and not me['is_admin'] %}<form method="post" action="{{ url_for('buy_course',course_id=c['id']) }}"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn primary">شراء من المحفظة</button></form>
    {% elif not me %}<a class="btn primary" href="{{ url_for('login') }}">سجّل الدخول للاشتراك</a>{% endif %}</div>
    <section style="margin-top:22px"><div class="course-head"><div><h2>امتحانات الكورس</h2><p style="margin:4px 0">امتحانات مجمعة بأسئلة متعددة، وتظهر هنا داخل تبويب المادة.</p></div><span class="course-count">{{ exam_cards|length }} امتحان</span></div>
    <div class="grid">{% for item in exam_cards %}{% set e=item.exam %}<article class="card"><div class="cover">{{ '✅' if item.completed else ('📝' if item.unlocked else '🔒') }}</div><h3>{{ e['title'] }}</h3><p>{{ e['description'] or 'امتحان مجمع على محتوى الكورس.' }}</p>
    {% if item.completed %}<span class="status approved">تم التسليم</span>{% endif %}
    {% if item.unlocked and (owned or (me and me['is_admin'])) %}<p><a class="btn primary" href="{{ url_for('take_exam',exam_id=e['id']) }}">{{ 'عرض الامتحان' if me and me['is_admin'] else ('إعادة الامتحان' if item.completed else 'ابدأ الامتحان') }}</a></p>
    {% elif not owned and not (me and me['is_admin']) %}<p class="muted">اشترك في الكورس لفتح الامتحانات.</p>
    {% else %}<p><span class="status pending">مقفول مؤقتًا</span></p><p class="muted">سلّم «{{ item.previous_title }}» أولًا لفتح هذا الامتحان.</p>{% endif %}</article>
    {% else %}<div class="card" style="grid-column:1/-1"><p>لا توجد امتحانات مضافة لهذا الكورس حتى الآن.</p>{% if me and me['is_admin'] %}<a class="btn primary" href="{{ url_for('admin_exams') }}">إنشاء امتحان للكورس</a>{% endif %}</div>{% endfor %}</div></section>"""
    return page(render_template_string(body, c=c, lessons=lessons, owned=owned, exam_cards=exam_cards), c["title"])

@app.route("/buy/<int:course_id>", methods=["POST"])
@login_required
def buy_course(course_id):
    u = current_user()
    if u["is_admin"]:
        flash("حساب الإدارة لا يشتري كورسات.", "warning")
        return redirect(url_for("courses"))
    c = db().execute("SELECT * FROM courses WHERE id=? AND active=1", (course_id,)).fetchone()
    if not c:
        abort(404)
    existing = db().execute("SELECT id FROM enrollments WHERE user_id=? AND course_id=?", (u["id"], course_id)).fetchone()
    if existing:
        flash("الكورس مفعّل بالفعل.", "success")
    elif u["wallet"] < c["price"]:
        flash("رصيدك غير كافٍ. قدّم طلب شحن من صفحة المحفظة.", "warning")
        return redirect(url_for("wallet"))
    else:
        db().execute("UPDATE users SET wallet=wallet-? WHERE id=?", (c["price"], u["id"]))
        db().execute("INSERT INTO enrollments(user_id,course_id,created_at) VALUES(?,?,?)", (u["id"], course_id, now()))
        db().commit()
        flash("تم شراء الكورس وتفعيله بنجاح.", "success")
    return redirect(url_for("my_courses"))

@app.route("/my-courses")
@login_required
def my_courses():
    u = current_user()
    rows = db().execute("""SELECT c.* FROM courses c JOIN enrollments e ON e.course_id=c.id
        WHERE e.user_id=? ORDER BY e.id DESC""", (u["id"],)).fetchall()
    body = """<h1>كورساتي</h1><div class="grid">{% for c in courses %}<article class="card"><div class="cover">{{ c['cover_emoji'] }}</div><h3>{{ c['title'] }}</h3><p>{{ c['description'] }}</p><a class="btn primary" href="{{ url_for('course_detail',course_id=c['id']) }}">فتح الكورس</a></article>{% else %}<div class="card"><p>لم تشترك في كورسات بعد.</p><a class="btn primary" href="{{ url_for('courses') }}">تصفح الكورسات</a></div>{% endfor %}</div>"""
    return page(render_template_string(body, courses=rows), "كورساتي")

@app.route("/wallet", methods=["GET", "POST"])
@login_required
def wallet():
    u = current_user()
    if u["is_admin"]:
        return redirect(url_for("admin"))
    if request.method == "POST":
        try:
            amount = int(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        sender = request.form.get("sender_phone", "").strip()
        file = request.files.get("receipt")
        if amount < 10 or amount > 5000:
            flash("مبلغ الشحن يجب أن يكون من 10 إلى 5000 جنيه.", "warning")
        elif not re.fullmatch(r"[0-9+\-\s]{8,20}", sender):
            flash("اكتب رقم الهاتف الذي حوّلت منه بشكل صحيح.", "warning")
        else:
            filename = None
            if file and file.filename:
                safe = secure_filename(file.filename)
                ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
                if ext not in ALLOWED_IMAGE_EXTENSIONS:
                    flash("الإيصال يجب أن يكون صورة PNG أو JPG أو WEBP.", "warning")
                    return redirect(url_for("wallet"))
                filename = secrets.token_hex(12) + "." + ext
                file.save(UPLOAD_DIR / filename)
            db().execute("""INSERT INTO topups(user_id,amount,sender_phone,receipt_file,status,created_at)
                VALUES(?,?,?,?, 'pending', ?)""", (u["id"], amount, sender, filename, now()))
            db().commit()
            flash("تم إرسال طلب الشحن للمراجعة. لن يزيد الرصيد إلا بعد موافقة الإدارة.", "success")
            return redirect(url_for("wallet"))
    requests = db().execute("SELECT * FROM topups WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    body = """
    <h1>المحفظة والشحن</h1><div class="card" style="background:linear-gradient(110deg,#e1f8f4,#fff)"><span class="muted">رصيدك الحالي</span><h1 style="color:#078f8d">{{ u['wallet'] }} ج.م</h1>
    <p>رقم المحفظة للتحويل: <strong dir="ltr" style="font-size:20px;color:#12354a">{{ wallet_number }}</strong>
    <button type="button" class="btn" onclick="navigator.clipboard&&navigator.clipboard.writeText('{{ wallet_number }}').then(()=>alert('تم نسخ الرقم'))">نسخ الرقم</button></p></div>
    <div class="row"><section class="card"><h2>طلب شحن جديد</h2><div class="notice">حوّل من خلال تطبيق المحفظة الذي تستخدمه إلى الرقم أعلاه، ثم أرسل بيانات الطلب. الطلب لا يضيف رصيدًا تلقائيًا؛ الإدارة تراجع التحويل أولًا.</div>
    <form method="post" enctype="multipart/form-data"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
    <label>مبلغ التحويل بالجنيه</label><input class="field" type="number" name="amount" min="10" max="5000" required>
    <label>رقم الهاتف الذي حوّلت منه</label><input class="field" name="sender_phone" required>
    <label>صورة إيصال التحويل (اختياري)</label><input class="field" type="file" name="receipt" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp">
    <button class="btn primary">إرسال طلب الشحن</button></form></section>
    <section class="card"><h2>طلبات الشحن السابقة</h2>{% for x in requests %}<div style="padding:12px 0;border-bottom:1px solid #e5edf1"><strong>{{ x['amount'] }} ج.م</strong> — <span class="status {{ x['status'] }}">{{ {'pending':'قيد المراجعة','approved':'مقبول','rejected':'مرفوض'}.get(x['status'],x['status']) }}</span><p class="muted">{{ x['created_at'] }} · رقم التحويل: {{ x['sender_phone'] }}</p>{% if x['admin_note'] %}<p>{{ x['admin_note'] }}</p>{% endif %}</div>{% else %}<p>لا توجد طلبات شحن حتى الآن.</p>{% endfor %}</section></div>"""
    return page(render_template_string(body, u=u, requests=requests), "المحفظة")

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_receipt(filename):
    u = current_user()
    if not u["is_admin"]:
        abort(403)
    return send_from_directory(UPLOAD_DIR, filename)

def exam_access_state(user_id, exam):
    """Return whether an exam is unlocked and whether the student has completed it.
    Exams are sequenced independently within each course (and within general exams).
    """
    completed = db().execute(
        "SELECT 1 FROM results WHERE user_id=? AND exam_id=? LIMIT 1",
        (user_id, exam["id"]),
    ).fetchone() is not None
    previous = db().execute(
        "SELECT id,title FROM exams WHERE active=1 AND course_id IS ? AND id<? ORDER BY id DESC LIMIT 1",
        (exam["course_id"], exam["id"]),
    ).fetchone()
    if previous is None:
        return True, completed, None
    previous_completed = db().execute(
        "SELECT 1 FROM results WHERE user_id=? AND exam_id=? LIMIT 1",
        (user_id, previous["id"]),
    ).fetchone() is not None
    return previous_completed, completed, previous["title"]


@app.route("/exams")
@login_required
def exams():
    u = current_user()
    rows = db().execute("""SELECT x.*, c.title AS course_title FROM exams x
        LEFT JOIN courses c ON c.id=x.course_id WHERE x.active=1 ORDER BY x.course_id, x.id""").fetchall()
    cards = []
    for e in rows:
        unlocked, completed, previous_title = exam_access_state(u["id"], e)
        cards.append({"exam": e, "unlocked": unlocked, "completed": completed,
                      "previous_title": previous_title})
    body = """<h1>الامتحانات</h1><p>الامتحانات بتتفتح بالترتيب. لازم تسلّم الامتحان الحالي علشان تفتح اللي بعده في نفس المادة.</p><div class="grid">
    {% for item in cards %}{% set e=item.exam %}<article class="card"><div class="cover">{{ '✅' if item.completed else ('☑️' if item.unlocked else '🔒') }}</div><h3>{{ e['title'] }}</h3><p>{{ e['description'] }}</p><div class="muted">{{ e['course_title'] or 'امتحان عام' }}</div>
    {% if item.completed %}<p><span class="status approved">تم التسليم</span></p>{% endif %}
    {% if item.unlocked %}<p><a class="btn primary" href="{{ url_for('take_exam',exam_id=e['id']) }}">{{ 'إعادة الامتحان' if item.completed else 'ابدأ الامتحان' }}</a></p>
    {% else %}<p><span class="status pending">مقفول مؤقتًا</span></p><p class="muted">سلّم «{{ item.previous_title }}» أولًا لفتح هذا الامتحان.</p>{% endif %}</article>{% else %}<p>لا توجد امتحانات متاحة حاليًا.</p>{% endfor %}</div>"""
    return page(render_template_string(body, cards=cards), "الامتحانات")

@app.route("/exam/<int:exam_id>", methods=["GET", "POST"])
@login_required
def take_exam(exam_id):
    u = current_user()
    exam = db().execute("SELECT * FROM exams WHERE id=? AND active=1", (exam_id,)).fetchone()
    if not exam:
        abort(404)
    if exam["course_id"] is not None and not u["is_admin"]:
        enrolled = db().execute("SELECT 1 FROM enrollments WHERE user_id=? AND course_id=?",
                                (u["id"], exam["course_id"])).fetchone()
        if not enrolled:
            flash("لازم تشترك في الكورس الأول علشان تدخل امتحاناته.", "warning")
            return redirect(url_for("course_detail", course_id=exam["course_id"]))
    unlocked, completed, previous_title = exam_access_state(u["id"], exam)
    if not unlocked:
        flash(f"الامتحان ده مقفول. لازم تسلّم امتحان «{previous_title}» الأول.", "warning")
        return redirect(url_for("exams"))
    questions = list(db().execute("SELECT * FROM questions WHERE exam_id=? ORDER BY id", (exam_id,)).fetchall())
    if request.method == "GET":
        random.shuffle(questions)
    if request.method == "POST":
        if not questions:
            flash("الامتحان لا يحتوي على أسئلة.", "warning")
            return redirect(url_for("exams"))
        score = 0
        review = []
        for q in questions:
            try:
                answer = int(request.form.get(f"q{q['id']}", "-1"))
            except ValueError:
                answer = -1
            correct = answer == q["correct"]
            score += int(correct)
            review.append((q, answer, correct))
        db().execute("INSERT INTO results(user_id,exam_id,score,total,created_at) VALUES(?,?,?,?,?)",
                     (u["id"], exam_id, score, len(questions), now()))
        db().commit()
        body = """<style>.result-hero{border-radius:18px;padding:24px;background:linear-gradient(130deg,#e6f8f4,#edf5ff);border:1px solid #cfe8e8;margin-bottom:20px}.score-ring{font-size:32px;font-weight:900;color:#087f8c}.result-item{border:1px solid #dce5ef;border-radius:14px;padding:16px;margin:12px 0;background:#fff}.result-item.correct-answer{border-right:5px solid #249767}.result-item.wrong-answer{border-right:5px solid #c45c5c}.result-tag{display:inline-block;border-radius:8px;padding:5px 9px;font-size:12px;font-weight:800}.result-good{background:#e2f7eb;color:#207747}.result-bad{background:#ffeded;color:#9b3838}</style>
        <section class="result-hero"><div style="font-weight:900;color:#087f8c">✦ انتهى الامتحان</div><h1 style="margin:8px 0">نتيجة {{ exam['title'] }}</h1><div class="score-ring">{{ score }} من {{ total }}</div><p style="margin:4px 0">نسبتك: {{ ((score / total) * 100)|round|int }}% · راجع الإجابات والتفسيرات عشان تستفيد من المحاولة.</p><div class="exam-progress"><div class="exam-progress-fill" style="width:{{ (score / total * 100) if total else 0 }}%"></div></div></section>
        <h2>مراجعة الإجابات</h2>{% for q,answer,correct in review %}<article class="result-item {{ 'correct-answer' if correct else 'wrong-answer' }}"><div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap"><strong>{{ loop.index }}. {{ q['question'] }}</strong><span class="result-tag {{ 'result-good' if correct else 'result-bad' }}">{{ 'إجابة صحيحة ✓' if correct else 'إجابة غير صحيحة' }}</span></div><p>إجابتك: {% if answer in [0,1,2,3] %}{{ ['أ','ب','ج','د'][answer] }} — {{ [q['a'],q['b'],q['c'],q['d']][answer] }}{% else %}لم تجب عن السؤال{% endif %}</p>{% if not correct %}<p><strong>الإجابة الصحيحة:</strong> {{ ['أ','ب','ج','د'][q['correct']] }} — {{ [q['a'],q['b'],q['c'],q['d']][q['correct']] }}</p>{% endif %}{% if q['explanation'] %}<div class="notice" style="margin-bottom:0"><strong>تفسير الإجابة:</strong> {{ q['explanation'] }}</div>{% endif %}</article>{% endfor %}
        <div class="actions">{% if exam['course_id'] %}<a class="btn primary" href="{{ url_for('course_detail',course_id=exam['course_id']) }}">العودة للكورس</a>{% endif %}<a class="btn primary" href="{{ url_for('exams') }}">كل الامتحانات</a></div>"""
        return page(render_template_string(body, exam=exam, score=score, total=len(questions), review=review), "نتيجة الامتحان")
    body = """<style>
    .exam-shell{max-width:920px;margin:auto}.exam-top{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap}
    .exam-meta{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.exam-pill{display:inline-flex;align-items:center;gap:6px;padding:7px 11px;border-radius:999px;background:#edf6f8;color:#35576c;font-size:12px;font-weight:800}
    .exam-progress{height:9px;background:#e6edf3;border-radius:99px;overflow:hidden;margin:14px 0 22px}.exam-progress-fill{height:100%;width:0;background:linear-gradient(90deg,#0b9b91,#087f8c);border-radius:99px;transition:width .2s}
    .question-panel{border:1px solid #dce7ef;border-radius:18px;padding:clamp(16px,3vw,28px);background:linear-gradient(180deg,#fff,#fbfdff);box-shadow:0 8px 22px rgba(24,55,85,.055)}
    .question-head{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:14px}.question-number{background:#e4f5f3;color:#087f8c;padding:7px 11px;border-radius:10px;font-size:12px;font-weight:900}
    .question-text{font-size:clamp(18px,2.6vw,23px);line-height:1.9;margin:10px 0 20px;color:#18324b}
    .answer-options{display:grid;grid-template-columns:1fr 1fr;gap:11px}.answer-option{position:relative;display:flex;align-items:flex-start;gap:11px;padding:14px;border:1.5px solid #dce5ef;border-radius:13px;background:#fff;cursor:pointer;min-height:62px;transition:border-color .15s,background .15s,transform .15s}
    .answer-option:hover{border-color:#8bcbd0;background:#f7fcfc;transform:translateY(-1px)}.answer-option:has(input:checked){border-color:#078f8d;background:#eaf8f6;box-shadow:0 0 0 2px #078f8d16}
    .answer-option input{accent-color:#087f8c;margin-top:4px;flex:0 0 auto;width:17px;height:17px}.option-letter{display:inline-flex;align-items:center;justify-content:center;min-width:27px;height:27px;border-radius:8px;background:#edf2f7;color:#435b70;font-weight:900;font-size:12px}
    .answer-option:has(input:checked) .option-letter{background:#087f8c;color:#fff}.option-text{line-height:1.8;font-weight:600;color:#29445b}
    .exam-nav{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:18px}.exam-nav-side{display:flex;gap:8px;flex-wrap:wrap}
    .question-map{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0}.question-jump{width:36px;height:36px;border:1px solid #dce5ef;background:#fff;border-radius:10px;cursor:pointer;color:#536a7e;font-weight:800}.question-jump.current{background:#087f8c;color:#fff;border-color:#087f8c}.question-jump.answered{border-color:#70c9b9;background:#e6f7f2;color:#176e63}.question-jump.current.answered{background:#087f8c;color:#fff}
    .exam-submitbar{position:sticky;bottom:10px;margin-top:18px;padding:13px;background:rgba(255,255,255,.96);backdrop-filter:blur(10px);border:1px solid #dce5ef;border-radius:15px;box-shadow:0 8px 24px rgba(24,55,85,.12);display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
    @media(max-width:650px){.answer-options{grid-template-columns:1fr}.exam-submitbar{bottom:6px}.exam-submitbar .btn{width:100%}.exam-top{align-items:flex-start}}
    </style>
    <div class="exam-shell"><section class="card">
    <div class="exam-top"><div><div style="color:#087f8c;font-size:12px;font-weight:900">✦ وضع الامتحان</div><h1 style="margin:5px 0">{{ exam['title'] }}</h1><p style="margin:0">{{ exam['description'] or 'اقرأ السؤال كويس، اختار إجابة، وتقدر تراجع إجاباتك قبل التسليم.' }}</p></div>
    <span class="exam-pill">📝 {{ questions|length }} سؤال</span></div>
    {% if questions %}<div class="exam-meta"><span class="exam-pill">🎯 اختار إجابة لكل سؤال</span><span class="exam-pill">↔️ تقدر تتنقل بين الأسئلة</span></div>
    <div class="exam-progress" aria-label="نسبة الإجابة"><div class="exam-progress-fill" id="examProgressFill"></div></div>
    <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><span class="muted" id="answeredCounter">تمت الإجابة عن 0 من {{ questions|length }}</span><span class="muted" id="progressPercent">0%</span></div>
    <div class="question-map" id="questionMap" aria-label="التنقل بين الأسئلة">{% for q in questions %}<button class="question-jump" type="button" data-jump="{{ loop.index0 }}" aria-label="السؤال {{ loop.index }}">{{ loop.index }}</button>{% endfor %}</div>
    <form method="post" id="examForm"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
    {% for q in questions %}<section class="question-panel exam-question" data-question="{{ loop.index0 }}" {% if not loop.first %}hidden{% endif %}>
      <div class="question-head"><span class="question-number">السؤال {{ loop.index }} من {{ questions|length }}</span><span class="muted">اختيار واحد</span></div>
      <h2 class="question-text">{{ q['question'] }}</h2>
      <div class="answer-options">{% for opt in [q['a'],q['b'],q['c'],q['d']] %}<label class="answer-option"><input type="radio" name="q{{ q['id'] }}" value="{{ loop.index0 }}"><span class="option-letter">{{ ['أ','ب','ج','د'][loop.index0] }}</span><span class="option-text">{{ opt }}</span></label>{% endfor %}</div>
    </section>{% endfor %}
    <div class="exam-nav"><div class="exam-nav-side"><button class="btn" type="button" id="prevQuestion">→ السؤال السابق</button><button class="btn primary" type="button" id="nextQuestion">السؤال التالي ←</button></div><span class="muted" id="currentQuestionLabel">السؤال 1 من {{ questions|length }}</span></div>
    <div class="exam-submitbar"><div><strong>راجع إجاباتك قبل التسليم</strong><div class="muted" id="submitHint">تقدر ترجع لأي سؤال من الأرقام فوق.</div></div><button class="btn primary" type="submit" id="submitExam">تسليم الامتحان ✓</button></div>
    </form>
    <script>
    (()=>{const form=document.getElementById('examForm');if(!form)return;
      const panels=[...document.querySelectorAll('.exam-question')], jumps=[...document.querySelectorAll('.question-jump')];
      const prev=document.getElementById('prevQuestion'),next=document.getElementById('nextQuestion');
      let current=0;
      function answered(i){return !!panels[i].querySelector('input[type=radio]:checked')}
      function refresh(){const count=panels.filter((_,i)=>answered(i)).length, pct=Math.round(count/panels.length*100);
        document.getElementById('answeredCounter').textContent=`تمت الإجابة عن ${count} من ${panels.length}`;
        document.getElementById('progressPercent').textContent=pct+'%';
        document.getElementById('examProgressFill').style.width=pct+'%';
        jumps.forEach((b,i)=>{b.classList.toggle('current',i===current);b.classList.toggle('answered',answered(i));});
        document.getElementById('currentQuestionLabel').textContent=`السؤال ${current+1} من ${panels.length}`;
        prev.disabled=current===0;prev.style.opacity=prev.disabled?'.45':'1';
        next.textContent=current===panels.length-1?'الرجوع لأول سؤال ↶':'السؤال التالي ←';
      }
      function show(i){current=(i+panels.length)%panels.length;panels.forEach((p,n)=>p.hidden=n!==current);refresh();window.scrollTo({top:0,behavior:'smooth'});}
      prev.addEventListener('click',()=>show(current-1));next.addEventListener('click',()=>show(current+1));
      jumps.forEach((b,i)=>b.addEventListener('click',()=>show(i)));
      form.addEventListener('change',refresh);
      form.addEventListener('submit',e=>{const count=panels.filter((_,i)=>answered(i)).length;
        const message=count<panels.length?`أجبت عن ${count} من ${panels.length} سؤال. هل تريد التسليم مع وجود أسئلة بلا إجابة؟`:'هل أنت متأكد من تسليم الامتحان؟ لن يمكنك تعديل الإجابات بعد التسليم.';
        if(!window.confirm(message)){e.preventDefault();return;}
      });
      show(0);
    })();
    </script>
    {% else %}<p>الإدارة لم تضف أسئلة بعد.</p>{% endif %}</section></div>"""
    return page(render_template_string(body, exam=exam, questions=questions), exam["title"])

@app.route("/my-progress")
@login_required
def my_progress():
    u = current_user()
    rows = db().execute("""SELECT r.*, e.title AS exam_title, c.title AS course_title
        FROM results r JOIN exams e ON e.id=r.exam_id
        LEFT JOIN courses c ON c.id=e.course_id
        WHERE r.user_id=? ORDER BY r.id DESC LIMIT 100""", (u["id"],)).fetchall()
    avg = round(sum((r["score"] / r["total"] * 100) if r["total"] else 0 for r in rows) / len(rows)) if rows else 0
    body = """<h1>تقدمي ونتائجي</h1><div class="grid"><div class="card"><span class="muted">عدد المحاولات</span><h2>{{ rows|length }}</h2></div><div class="card"><span class="muted">متوسط النتيجة</span><h2>{{ avg }}%</h2></div></div>
    <section class="card" style="margin-top:15px;overflow-x:auto"><h2>سجل الامتحانات</h2><table><tr><th>الامتحان</th><th>الكورس</th><th>النتيجة</th><th>النسبة</th><th>التاريخ</th></tr>
    {% for r in rows %}<tr><td>{{ r['exam_title'] }}</td><td>{{ r['course_title'] or 'عام' }}</td><td>{{ r['score'] }} / {{ r['total'] }}</td><td>{{ ((r['score']/r['total']*100)|round|int) if r['total'] else 0 }}%</td><td>{{ r['created_at'] }}</td></tr>{% else %}<tr><td colspan="5">لسه مفيش محاولات مسجلة. ابدأ أول امتحان ليك!</td></tr>{% endfor %}</table></section>"""
    return page(render_template_string(body, rows=rows, avg=avg), "تقدمي ونتائجي")

@app.route("/forum", methods=["GET", "POST"])
@login_required
def forum():
    u = current_user()
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:100]
        body_text = request.form.get("body", "").strip()[:1500]
        if not title or not body_text:
            flash("اكتب عنوان المشاركة وتفاصيلها.", "warning")
        else:
            db().execute("INSERT INTO posts(user_id,title,body,created_at) VALUES(?,?,?,?)", (u["id"], title, body_text, now()))
            db().commit()
            flash("تم نشر المشاركة.", "success")
            return redirect(url_for("forum"))
    posts = db().execute("""SELECT p.*,u.name FROM posts p JOIN users u ON u.id=p.user_id
        ORDER BY p.id DESC LIMIT 100""").fetchall()
    body = """<h1>مجتمع الطلاب</h1><div class="row"><section class="card"><h2>شارك سؤالًا</h2>
    <form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><label>عنوان المشاركة</label><input class="field" name="title" maxlength="100" required>
    <label>التفاصيل</label><textarea class="field" name="body" rows="4" maxlength="1500" required></textarea><button class="btn primary">نشر المشاركة</button></form></section>
    <section class="card"><h2>أحدث المشاركات</h2>{% for p in posts %}<div style="padding:12px 0;border-bottom:1px solid #e5edf1"><strong>{{ p['title'] }}</strong><p>{{ p['body'] }}</p><span class="muted">{{ p['name'] }} · {{ p['created_at'] }}</span></div>{% else %}<p>لا توجد مشاركات بعد.</p>{% endfor %}</section></div>"""
    return page(render_template_string(body, posts=posts), "مجتمع الطلاب")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    u = current_user()
    if u and u["is_admin"]:
        return redirect(url_for("admin"))
    if request.method == "POST":
        # Admin account uses phone field "admin"; password is set on first run.
        user = db().execute("SELECT * FROM users WHERE phone='admin' AND is_admin=1").fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("admin"))
        flash("كلمة مرور الإدارة غير صحيحة.", "warning")
    body = """<div class="card" style="max-width:500px;margin:auto"><h2>دخول الإدارة</h2><p>اسم المستخدم الافتراضي: admin</p>
    <form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><label>كلمة مرور الإدارة</label><input class="field" type="password" name="password" required><button class="btn primary">دخول الإدارة</button></form>
    <div class="notice">غيّر كلمة المرور الافتراضية قبل استخدام المنصة بجدية.</div></div>"""
    return page(render_template_string(body), "دخول الإدارة")

@app.route("/admin")
@admin_required
def admin():
    conn = db()
    counts = {
        "students": conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=0").fetchone()[0],
        "courses": conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
        "pending": conn.execute("SELECT COUNT(*) FROM topups WHERE status='pending'").fetchone()[0],
        "exams": conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0],
    }
    body = """<h1>لوحة إدارة المنصة</h1><p>من هنا تقدر تدير الكورسات والامتحانات وطلبات شحن المحافظ.</p>
    <div class="grid"><div class="card"><h3>الطلاب</h3><h1>{{ counts.students }}</h1></div><div class="card"><h3>الكورسات</h3><h1>{{ counts.courses }}</h1></div><div class="card"><h3>طلبات شحن معلّقة</h3><h1>{{ counts.pending }}</h1></div></div>
    <div class="grid" style="margin-top:15px"><div class="card"><h3>📚 إدارة الكورسات</h3><p>إضافة وتعديل وحذف الكورسات والأسعار والدروس.</p><a class="btn primary" href="{{ url_for('admin_courses') }}">إدارة الكورسات</a></div>
    <div class="card"><h3>🧾 طلبات الشحن</h3><p>راجع الإيصال ووافق أو ارفض الطلب.</p><a class="btn primary" href="{{ url_for('admin_topups') }}">مراجعة الشحن</a></div>
    <div class="card"><h3>☑️ الامتحانات</h3><p>إنشاء امتحان مجمع لكل كورس وإضافة عدد الأسئلة الذي تريده.</p><a class="btn primary" href="{{ url_for('admin_exams') }}">إدارة الامتحانات</a></div><div class="card"><h3>👨‍🎓 إدارة الطلاب</h3><p>عرض الحسابات وإيقاف أو إعادة تفعيل حساب طالب.</p><a class="btn primary" href="{{ url_for('admin_students') }}">إدارة الطلاب</a></div><div class="card"><h3>📊 التقارير</h3><p>متابعة الدرجات والمحاولات المسجلة.</p><a class="btn primary" href="{{ url_for('admin_reports') }}">تقارير النتائج</a></div><div class="card"><h3>💬 مراجعة المجتمع</h3><p>حذف المشاركات غير المناسبة.</p><a class="btn primary" href="{{ url_for('admin_posts') }}">إدارة المشاركات</a></div><div class="card"><h3>💾 نسخة احتياطية</h3><p>تحميل نسخة من قاعدة البيانات.</p><a class="btn" href="{{ url_for('admin_backup') }}">تنزيل نسخة</a></div></div>
    <div class="card" style="margin-top:15px"><h3>معلومات النظام</h3><p>قاعدة البيانات: sahelha.db · صور الإيصالات: مجلد uploads · رقم المحفظة الظاهر للطلاب: {{ wallet_number }}</p></div>"""
    return page(render_template_string(body, counts=counts), "لوحة الإدارة")

@app.route("/admin/students")
@admin_required
def admin_students():
    rows = db().execute("""SELECT u.id,u.name,u.phone,u.wallet,u.disabled,u.created_at,
        (SELECT COUNT(*) FROM results r WHERE r.user_id=u.id) AS attempts
        FROM users u WHERE u.is_admin=0 ORDER BY u.id DESC""").fetchall()
    body = """<h1>إدارة الطلاب</h1><div class="card" style="overflow-x:auto"><table><tr><th>الطالب</th><th>الهاتف</th><th>الرصيد</th><th>المحاولات</th><th>الحالة</th><th>إجراء</th></tr>
    {% for u in rows %}<tr><td>{{ u['name'] }}<div class="muted">انضم: {{ u['created_at'] }}</div></td><td>{{ u['phone'] }}</td><td>{{ u['wallet'] }} ج.م</td><td>{{ u['attempts'] }}</td><td>{{ 'موقوف' if u['disabled'] else 'نشط' }}</td><td><form method="post" action="{{ url_for('admin_student_toggle',user_id=u['id']) }}"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">{{ 'إعادة تفعيل' if u['disabled'] else 'إيقاف الحساب' }}</button></form></td></tr>{% else %}<tr><td colspan="6">لا يوجد طلاب مسجلون.</td></tr>{% endfor %}</table></div>"""
    return page(render_template_string(body, rows=rows), "إدارة الطلاب")

@app.route("/admin/student/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_student_toggle(user_id):
    row = db().execute("SELECT disabled FROM users WHERE id=? AND is_admin=0", (user_id,)).fetchone()
    if not row:
        abort(404)
    db().execute("UPDATE users SET disabled=? WHERE id=?", (0 if row["disabled"] else 1, user_id))
    db().commit()
    flash("تم تحديث حالة حساب الطالب.", "success")
    return redirect(url_for("admin_students"))

@app.route("/admin/reports")
@admin_required
def admin_reports():
    rows = db().execute("""SELECT r.*,u.name,u.phone,e.title AS exam_title,c.title AS course_title
        FROM results r JOIN users u ON u.id=r.user_id JOIN exams e ON e.id=r.exam_id
        LEFT JOIN courses c ON c.id=e.course_id ORDER BY r.id DESC LIMIT 300""").fetchall()
    body = """<h1>تقارير النتائج</h1><p>آخر 300 محاولة مسجلة.</p><div class="card" style="overflow-x:auto"><table><tr><th>الطالب</th><th>الامتحان</th><th>الكورس</th><th>الدرجة</th><th>النسبة</th><th>التاريخ</th></tr>
    {% for r in rows %}<tr><td>{{ r['name'] }}<div class="muted">{{ r['phone'] }}</div></td><td>{{ r['exam_title'] }}</td><td>{{ r['course_title'] or 'عام' }}</td><td>{{ r['score'] }} / {{ r['total'] }}</td><td>{{ ((r['score']/r['total']*100)|round|int) if r['total'] else 0 }}%</td><td>{{ r['created_at'] }}</td></tr>{% else %}<tr><td colspan="6">لا توجد نتائج حتى الآن.</td></tr>{% endfor %}</table></div>"""
    return page(render_template_string(body, rows=rows), "تقارير النتائج")

@app.route("/admin/posts")
@admin_required
def admin_posts():
    rows = db().execute("SELECT p.*,u.name FROM posts p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 300").fetchall()
    body = """<h1>إدارة مشاركات المجتمع</h1><div class="card">{% for p in rows %}<article style="padding:14px 0;border-bottom:1px solid #dce5ef"><strong>{{ p['title'] }}</strong><p>{{ p['body'] }}</p><span class="muted">{{ p['name'] }} · {{ p['created_at'] }}</span><form method="post" action="{{ url_for('admin_post_delete',post_id=p['id']) }}" onsubmit="return confirm('حذف المشاركة؟')"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">حذف المشاركة</button></form></article>{% else %}<p>لا توجد مشاركات.</p>{% endfor %}</div>"""
    return page(render_template_string(body, rows=rows), "إدارة المشاركات")

@app.route("/admin/post/<int:post_id>/delete", methods=["POST"])
@admin_required
def admin_post_delete(post_id):
    db().execute("DELETE FROM posts WHERE id=?", (post_id,))
    db().commit()
    flash("تم حذف المشاركة.", "success")
    return redirect(url_for("admin_posts"))

@app.route("/admin/backup")
@admin_required
def admin_backup():
    backup_path = BASE_DIR / ("sahelha-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return send_file(backup_path, as_attachment=True, download_name=backup_path.name, mimetype="application/octet-stream")

@app.route("/admin/courses", methods=["GET", "POST"])
@admin_required
def admin_courses():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:120]
        subject = request.form.get("subject", "").strip()[:80]
        description = request.form.get("description", "").strip()[:1000]
        lessons = request.form.get("lessons", "").strip()[:5000]
        emoji = request.form.get("emoji", "📚").strip()[:8] or "📚"
        try:
            price = int(request.form.get("price", "0"))
        except ValueError:
            price = -1
        cid = request.form.get("course_id", "").strip()
        if not title or not subject or price < 0 or price > 100000:
            flash("راجع اسم الكورس والمادة والسعر.", "warning")
        else:
            if cid:
                db().execute("""UPDATE courses SET title=?,subject=?,description=?,price=?,cover_emoji=?,lessons=? WHERE id=?""",
                             (title, subject, description, price, emoji, lessons, int(cid)))
                flash("تم تحديث الكورس.", "success")
            else:
                db().execute("""INSERT INTO courses(title,subject,description,price,cover_emoji,lessons,active,created_at)
                    VALUES(?,?,?,?,?,?,1,?)""", (title, subject, description, price, emoji, lessons, now()))
                flash("تمت إضافة الكورس.", "success")
            db().commit()
        return redirect(url_for("admin_courses"))
    edit = None
    if request.args.get("edit", "").isdigit():
        edit = db().execute("SELECT * FROM courses WHERE id=?", (int(request.args["edit"]),)).fetchone()
    rows = db().execute("SELECT * FROM courses ORDER BY id DESC").fetchall()
    body = """<h1>إدارة الكورسات</h1><div class="row"><section class="card"><h2>{{ 'تعديل كورس' if edit else 'إضافة كورس جديد' }}</h2>
    <form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><input type="hidden" name="course_id" value="{{ edit['id'] if edit else '' }}">
    <label>اسم الكورس</label><input class="field" name="title" required value="{{ edit['title'] if edit else '' }}">
    <label>اسم المادة</label><input class="field" name="subject" required value="{{ edit['subject'] if edit else '' }}">
    <label>وصف الكورس</label><textarea class="field" name="description" rows="3">{{ edit['description'] if edit else '' }}</textarea>
    <label>السعر بالجنيه</label><input class="field" type="number" min="0" name="price" required value="{{ edit['price'] if edit else 0 }}">
    <label>رمز تعبيري للغلاف</label><input class="field" name="emoji" value="{{ edit['cover_emoji'] if edit else '📚' }}">
    <label>الدروس (كل درس في سطر مستقل)</label><textarea class="field" name="lessons" rows="5">{{ edit['lessons'] if edit else '' }}</textarea>
    <button class="btn primary">حفظ الكورس</button>{% if edit %} <a class="btn" href="{{ url_for('admin_courses') }}">إلغاء</a>{% endif %}</form></section>
    <section class="card"><h2>الكورسات الحالية</h2><div style="overflow-x:auto"><table><tr><th>الكورس</th><th>السعر</th><th>الإجراءات</th></tr>{% for c in courses %}<tr><td>{{ c['title'] }}<div class="muted">{{ c['subject'] }}</div></td><td>{{ c['price'] }} ج.م</td><td><div class="actions"><a class="btn" href="{{ url_for('admin_courses',edit=c['id']) }}">تعديل</a>
    <form method="post" action="{{ url_for('admin_course_toggle',course_id=c['id']) }}"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">{{ 'إخفاء' if c['active'] else 'تفعيل' }}</button></form>
    <form method="post" action="{{ url_for('admin_course_delete',course_id=c['id']) }}" onsubmit="return confirm('حذف الكورس نهائيًا؟')"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">حذف</button></form></div></td></tr>{% endfor %}</table></div></section></div>"""
    return page(render_template_string(body, edit=edit, courses=rows), "إدارة الكورسات")

@app.route("/admin/course/<int:course_id>/toggle", methods=["POST"])
@admin_required
def admin_course_toggle(course_id):
    row = db().execute("SELECT active FROM courses WHERE id=?", (course_id,)).fetchone()
    if row:
        db().execute("UPDATE courses SET active=? WHERE id=?", (0 if row["active"] else 1, course_id))
        db().commit()
        flash("تم تحديث حالة الكورس.", "success")
    return redirect(url_for("admin_courses"))

@app.route("/admin/course/<int:course_id>/delete", methods=["POST"])
@admin_required
def admin_course_delete(course_id):
    db().execute("DELETE FROM courses WHERE id=?", (course_id,))
    db().commit()
    flash("تم حذف الكورس.", "success")
    return redirect(url_for("admin_courses"))

@app.route("/admin/topups")
@admin_required
def admin_topups():
    rows = db().execute("""SELECT t.*,u.name,u.phone FROM topups t JOIN users u ON u.id=t.user_id ORDER BY
        CASE t.status WHEN 'pending' THEN 0 ELSE 1 END, t.id DESC""").fetchall()
    body = """<h1>طلبات شحن المحافظ</h1><p>راجع إثبات التحويل قبل الموافقة. الموافقة تضيف الرصيد مرة واحدة فقط.</p><div class="card" style="overflow-x:auto"><table><tr><th>الطالب</th><th>المبلغ</th><th>رقم المُحوِّل</th><th>الإيصال</th><th>الحالة</th><th>الإجراء</th></tr>
    {% for t in rows %}<tr><td>{{ t['name'] }}<div class="muted">{{ t['phone'] }}</div><div class="muted">{{ t['created_at'] }}</div></td><td>{{ t['amount'] }} ج.م</td><td>{{ t['sender_phone'] }}</td><td>{% if t['receipt_file'] %}<a href="{{ url_for('uploaded_receipt',filename=t['receipt_file']) }}" target="_blank">عرض الإيصال</a>{% else %}لم يرفق{% endif %}</td>
    <td><span class="status {{ t['status'] }}">{{ {'pending':'قيد المراجعة','approved':'مقبول','rejected':'مرفوض'}.get(t['status'],t['status']) }}</span>{% if t['admin_note'] %}<div class="muted">{{ t['admin_note'] }}</div>{% endif %}</td><td>{% if t['status']=='pending' %}<form method="post" action="{{ url_for('admin_topup_review',topup_id=t['id']) }}"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><input class="field" name="note" placeholder="ملاحظة (اختياري)" style="min-width:130px"><button class="btn primary" name="decision" value="approve">موافقة وإضافة الرصيد</button> <button class="btn" name="decision" value="reject">رفض</button></form>{% else %}تمت المراجعة{% endif %}</td></tr>{% endfor %}</table></div>"""
    return page(render_template_string(body, rows=rows), "طلبات الشحن")

@app.route("/admin/topup/<int:topup_id>/review", methods=["POST"])
@admin_required
def admin_topup_review(topup_id):
    conn = db()
    t = conn.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
    if not t:
        abort(404)
    if t["status"] != "pending":
        flash("تمت مراجعة الطلب بالفعل.", "warning")
        return redirect(url_for("admin_topups"))
    decision = request.form.get("decision")
    note = request.form.get("note", "").strip()[:500]
    if decision == "approve":
        cur = conn.execute("UPDATE topups SET status='approved',admin_note=?,reviewed_at=? WHERE id=? AND status='pending'",
                           (note, now(), topup_id))
        if cur.rowcount == 1:
            conn.execute("UPDATE users SET wallet=wallet+? WHERE id=?", (t["amount"], t["user_id"]))
            conn.commit()
            flash("تمت الموافقة وإضافة الرصيد مرة واحدة.", "success")
        else:
            conn.rollback()
            flash("تمت مراجعة الطلب بالفعل ولم يُضف الرصيد مرة أخرى.", "warning")
    elif decision == "reject":
        cur = conn.execute("UPDATE topups SET status='rejected',admin_note=?,reviewed_at=? WHERE id=? AND status='pending'",
                           (note or "لم تتم الموافقة على الطلب.", now(), topup_id))
        conn.commit()
        flash("تم رفض الطلب." if cur.rowcount == 1 else "تمت مراجعة الطلب بالفعل.", "success" if cur.rowcount == 1 else "warning")
    else:
        flash("اختيار غير صحيح.", "warning")
    return redirect(url_for("admin_topups"))

@app.route("/admin/exams", methods=["GET", "POST"])
@admin_required
def admin_exams():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:150]
        description = request.form.get("description", "").strip()[:1000]
        course_id = request.form.get("course_id", "").strip()
        course_id = int(course_id) if course_id.isdigit() else None
        if not title:
            flash("اكتب اسم الامتحان.", "warning")
        else:
            db().execute("INSERT INTO exams(course_id,title,description,active,created_at) VALUES(?,?,?,1,?)",
                         (course_id, title, description, now()))
            db().commit()
            flash("تم إنشاء الامتحان. أضف الأسئلة من القائمة.", "success")
        return redirect(url_for("admin_exams"))
    rows = db().execute("""SELECT e.*,c.title AS course_title,
        (SELECT COUNT(*) FROM questions q WHERE q.exam_id=e.id) AS question_count
        FROM exams e LEFT JOIN courses c ON c.id=e.course_id ORDER BY e.id DESC""").fetchall()
    courses = db().execute("SELECT id,title FROM courses ORDER BY title").fetchall()
    body = """<h1>إدارة الامتحانات</h1><div class="row"><section class="card"><h2>إنشاء امتحان مجمع</h2><p class="muted">أنشئ امتحانًا واحدًا ثم أضف له الأسئلة واحدًا تلو الآخر بلا عدد ثابت.</p><form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
    <label>اسم الامتحان</label><input class="field" name="title" required>
    <label>وصف الامتحان</label><textarea class="field" name="description" rows="3"></textarea>
    <label>الكورس الذي سيظهر الامتحان بداخله</label><select class="field" name="course_id"><option value="">امتحان عام (بدون كورس)</option>{% for c in courses %}<option value="{{ c['id'] }}">{{ c['title'] }}</option>{% endfor %}</select>
    <button class="btn primary">إنشاء الامتحان</button></form></section><section class="card"><h2>الامتحانات الحالية</h2><div style="overflow-x:auto"><table><tr><th>الامتحان</th><th>الأسئلة</th><th>الإجراءات</th></tr>{% for e in exams %}<tr><td>{{ e['title'] }}<div class="muted">{{ e['course_title'] or 'عام' }}</div></td><td>{{ e['question_count'] }}</td><td><div class="actions"><a class="btn primary" href="{{ url_for('admin_questions',exam_id=e['id']) }}">الأسئلة</a><form method="post" action="{{ url_for('admin_exam_toggle',exam_id=e['id']) }}"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">{{ 'إخفاء' if e['active'] else 'تفعيل' }}</button></form><form method="post" action="{{ url_for('admin_exam_delete',exam_id=e['id']) }}" onsubmit="return confirm('حذف الامتحان وأسئلته؟')"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">حذف</button></form></div></td></tr>{% endfor %}</table></div></section></div>"""
    return page(render_template_string(body, exams=rows, courses=courses), "إدارة الامتحانات")

@app.route("/admin/exam/<int:exam_id>/questions", methods=["GET", "POST"])
@admin_required
def admin_questions(exam_id):
    exam = db().execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
    if not exam:
        abort(404)
    if request.method == "POST":
        q = request.form.get("question", "").strip()[:1000]
        opts = [request.form.get(x, "").strip()[:500] for x in ("a", "b", "c", "d")]
        explanation = request.form.get("explanation", "").strip()[:1500]
        question_id = request.form.get("question_id", "").strip()
        try:
            correct = int(request.form.get("correct", "0"))
        except ValueError:
            correct = -1
        if not q or any(not x for x in opts) or correct not in range(4):
            flash("اكتب السؤال والاختيارات الأربعة وحدد الإجابة الصحيحة.", "warning")
        elif question_id and question_id.isdigit():
            existing = db().execute("SELECT id FROM questions WHERE id=? AND exam_id=?", (int(question_id), exam_id)).fetchone()
            if existing:
                db().execute("UPDATE questions SET question=?,a=?,b=?,c=?,d=?,correct=?,explanation=? WHERE id=? AND exam_id=?",
                             (q, *opts, correct, explanation, int(question_id), exam_id))
                db().commit()
                flash("تم تعديل السؤال.", "success")
            else:
                flash("السؤال المطلوب تعديله غير موجود.", "warning")
        else:
            db().execute("""INSERT INTO questions(exam_id,question,a,b,c,d,correct,explanation)
                VALUES(?,?,?,?,?,?,?,?)""", (exam_id, q, *opts, correct, explanation))
            db().commit()
            flash("تمت إضافة السؤال.", "success")
        return redirect(url_for("admin_questions", exam_id=exam_id))
    questions = db().execute("SELECT * FROM questions WHERE exam_id=? ORDER BY id", (exam_id,)).fetchall()
    edit_id = request.args.get("edit", "")
    edit_question = db().execute("SELECT * FROM questions WHERE id=? AND exam_id=?", (int(edit_id), exam_id)).fetchone() if edit_id.isdigit() else None
    body = """<style>.question-admin{padding:15px 0;border-bottom:1px solid #e5edf1}.question-admin:last-child{border:0}.admin-choice{display:inline-block;margin:4px;padding:5px 8px;border-radius:7px;background:#f0f5f8;font-size:12px}</style>
    <h1>أسئلة: {{ exam['title'] }}</h1><p>تقدر تضيف أي عدد من الأسئلة، وتعدّل السؤال أو الاختيارات أو الإجابة الصحيحة في أي وقت.</p><div class="row"><section class="card"><h2>{{ 'تعديل السؤال' if edit_question else 'إضافة سؤال للامتحان المجمع' }}</h2><p class="muted">اكتب السؤال والاختيارات الأربعة، وحدد الإجابة الصحيحة وأضف تفسيرًا يظهر للطالب بعد التسليم.</p><form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><input type="hidden" name="question_id" value="{{ edit_question['id'] if edit_question else '' }}">
    <label>نص السؤال</label><textarea class="field" name="question" rows="3" maxlength="1000" required>{{ edit_question['question'] if edit_question else '' }}</textarea>
    {% for letter in ['a','b','c','d'] %}<label>الاختيار {{ ['أ','ب','ج','د'][loop.index0] }}</label><input class="field" name="{{ letter }}" maxlength="500" required value="{{ edit_question[letter] if edit_question else '' }}">{% endfor %}
    <label>الإجابة الصحيحة</label><select class="field" name="correct">{% for i in range(4) %}<option value="{{ i }}" {% if edit_question and edit_question['correct']==i %}selected{% endif %}>الاختيار {{ ['الأول','الثاني','الثالث','الرابع'][i] }}</option>{% endfor %}</select>
    <label>تفسير الإجابة (يظهر بعد تسليم الامتحان)</label><textarea class="field" name="explanation" rows="3" maxlength="1500">{{ edit_question['explanation'] if edit_question else '' }}</textarea><button class="btn primary">{{ 'حفظ التعديلات' if edit_question else 'إضافة السؤال' }}</button>{% if edit_question %} <a class="btn" href="{{ url_for('admin_questions',exam_id=exam['id']) }}">إلغاء التعديل</a>{% endif %}</form></section>
    <section class="card"><h2>الأسئلة المضافة ({{ questions|length }})</h2>{% for q in questions %}<div class="question-admin"><strong>{{ loop.index }}. {{ q['question'] }}</strong><div>{% for letter in ['a','b','c','d'] %}<span class="admin-choice" {% if loop.index0==q['correct'] %}style="background:#e2f7eb;color:#207747"{% endif %}>{{ ['أ','ب','ج','د'][loop.index0] }}. {{ q[letter] }}</span>{% endfor %}</div><p class="muted">الإجابة الصحيحة: {{ ['أ','ب','ج','د'][q['correct']] }} · {{ q['explanation'] or 'لم يضف تفسير بعد.' }}</p><div class="actions"><a class="btn" href="{{ url_for('admin_questions',exam_id=exam['id'],edit=q['id']) }}">تعديل السؤال</a><form method="post" action="{{ url_for('admin_question_delete',question_id=q['id']) }}" onsubmit="return confirm('متأكد من حذف السؤال؟')"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="btn">حذف السؤال</button></form></div></div>{% else %}<p>لم تضف أسئلة بعد.</p>{% endfor %}</section></div><p><a class="btn" href="{{ url_for('admin_exams') }}">← العودة للامتحانات</a></p>"""
    return page(render_template_string(body, exam=exam, questions=questions, edit_question=edit_question), "أسئلة الامتحان")

@app.route("/admin/question/<int:question_id>/delete", methods=["POST"])
@admin_required
def admin_question_delete(question_id):
    q = db().execute("SELECT exam_id FROM questions WHERE id=?", (question_id,)).fetchone()
    if q:
        eid = q["exam_id"]
        db().execute("DELETE FROM questions WHERE id=?", (question_id,))
        db().commit()
        flash("تم حذف السؤال.", "success")
        return redirect(url_for("admin_questions", exam_id=eid))
    return redirect(url_for("admin_exams"))

@app.route("/admin/exam/<int:exam_id>/toggle", methods=["POST"])
@admin_required
def admin_exam_toggle(exam_id):
    row = db().execute("SELECT active FROM exams WHERE id=?", (exam_id,)).fetchone()
    if row:
        db().execute("UPDATE exams SET active=? WHERE id=?", (0 if row["active"] else 1, exam_id))
        db().commit()
        flash("تم تحديث حالة الامتحان.", "success")
    return redirect(url_for("admin_exams"))

@app.route("/admin/exam/<int:exam_id>/delete", methods=["POST"])
@admin_required
def admin_exam_delete(exam_id):
    db().execute("DELETE FROM exams WHERE id=?", (exam_id,))
    db().commit()
    flash("تم حذف الامتحان وأسئلته.", "success")
    return redirect(url_for("admin_exams"))

@app.errorhandler(400)
def bad_request(error):
    return page("<div class='card'><h1>الطلب غير صالح</h1><p>ارجع للصفحة السابقة وحدّثها ثم حاول مرة أخرى.</p></div>", "طلب غير صالح"), 400

@app.errorhandler(403)
def forbidden(_error):
    return page("<div class='card'><h1>غير مسموح</h1><p>لا تملك صلاحية الوصول للصفحة.</p></div>", "غير مسموح"), 403

@app.errorhandler(404)
def not_found(_error):
    return page("<div class='card'><h1>الصفحة غير موجودة</h1><p>الرابط غير صحيح أو أن العنصر تم حذفه.</p><a class='btn primary' href='/'>الرئيسية</a></div>", "غير موجود"), 404

if __name__ == "__main__":
    init_db()
    print("\\nسهلتهالك بكالوريا تعمل الآن")
    print("افتح: http://127.0.0.1:5000")
    print("الإدارة: http://127.0.0.1:5000/admin/login")
    if ADMIN_PASSWORD == "ChangeMe123!":
        print("تنبيه: غيّر كلمة مرور الإدارة الافتراضية قبل استخدام المنصة بجدية.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
