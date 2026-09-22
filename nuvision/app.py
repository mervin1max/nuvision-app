import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect, url_for,
    session, flash, send_from_directory, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "nuvision.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"doc", "docx", "pdf"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB per upload

# One-time secret code required to create the Discipline Master (DM) account.
# CHANGE THIS before you deploy the site for real use.
DM_SETUP_CODE = os.environ.get("NUVISION_DM_CODE", "NUVISION-DM-2026")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("NUVISION_SECRET_KEY", "dev-change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            nickname TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student','dm','viewer')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            application_start TEXT,
            application_end TEXT,
            campaign_start TEXT,
            voting_start TEXT,
            voting_end TEXT,
            school_name TEXT DEFAULT 'Nuvision High School'
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            winner_application_id INTEGER,
            winner_announced_at TEXT
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES users(id),
            position_id INTEGER NOT NULL REFERENCES positions(id),
            manifesto TEXT,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','interview_approved','rejected','candidate','not_selected')),
            feedback TEXT,
            updated_at TEXT,
            UNIQUE(student_id, position_id)
        );

        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES users(id),
            position_id INTEGER NOT NULL REFERENCES positions(id),
            application_id INTEGER NOT NULL REFERENCES applications(id),
            voted_at TEXT NOT NULL,
            UNIQUE(student_id, position_id)
        );
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO config (id, application_start, application_end, "
        "campaign_start, voting_start, voting_end) VALUES (1, NULL, NULL, NULL, NULL, NULL)"
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def get_config():
    return get_db().execute("SELECT * FROM config WHERE id = 1").fetchone()


def dm_exists():
    return get_db().execute("SELECT 1 FROM users WHERE role = 'dm' LIMIT 1").fetchone() is not None


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "config": get_config(), "now": datetime.now()}


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please log in to continue.", "error")
                return redirect(url_for("index"))
            if role and user["role"] != role:
                flash("You don't have access to that page.", "error")
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def phase_status():
    """Return a dict describing where we are in the election timeline,
    plus any warning banner that should be shown to students."""
    cfg = get_config()
    n = datetime.now()
    a_start, a_end = parse_dt(cfg["application_start"]), parse_dt(cfg["application_end"])
    c_start = parse_dt(cfg["campaign_start"])
    v_start, v_end = parse_dt(cfg["voting_start"]), parse_dt(cfg["voting_end"])

    info = {
        "applications_open": bool(a_start and a_end and a_start <= n <= a_end),
        "applications_upcoming": bool(a_start and n < a_start),
        "applications_closed": bool(a_end and n > a_end),
        "campaign_open": bool(c_start and (not v_end or n <= v_end) and n >= c_start),
        "voting_open": bool(v_start and v_end and v_start <= n <= v_end),
        "voting_closed": bool(v_end and n > v_end),
        "warning": None,
    }
    if info["applications_open"] and a_end:
        remaining = a_end - n
        if remaining <= timedelta(hours=1):
            info["warning"] = ("critical", f"Applications close in less than an hour! ({a_end.strftime('%b %d, %I:%M %p')})")
        elif remaining <= timedelta(hours=24):
            hrs = int(remaining.total_seconds() // 3600)
            info["warning"] = ("warning", f"Only about {hrs} hour(s) left to submit your application — deadline is {a_end.strftime('%b %d, %I:%M %p')}.")
        elif remaining <= timedelta(days=2):
            info["warning"] = ("notice", f"Applications close {a_end.strftime('%b %d, %I:%M %p')} — don't wait until the last minute!")
    return info


@app.template_filter("fmt")
def fmt_dt(value):
    dt = parse_dt(value)
    if not dt:
        return "Not set"
    return dt.strftime("%b %d, %Y — %I:%M %p")


# ---------------------------------------------------------------------------
# Public / auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    user = current_user()
    if user:
        if user["role"] == "dm":
            return redirect(url_for("dm_dashboard"))
        if user["role"] == "viewer":
            return redirect(url_for("viewer_results"))
        return redirect(url_for("student_dashboard"))
    return render_template("index.html", dm_exists=dm_exists())


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "notice")
    return redirect(url_for("index"))


# --- Student registration / login ------------------------------------------------

@app.route("/student/register", methods=["GET", "POST"])
def student_register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        nickname = request.form.get("nickname", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        error = None
        if not full_name or not nickname or not password:
            error = "Please fill in your full name, nickname, and password."
        elif len(nickname) < 3:
            error = "Your nickname must be at least 3 characters."
        elif len(password) < 4:
            error = "Your password must be at least 4 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            db = get_db()
            exists = db.execute("SELECT 1 FROM users WHERE nickname = ?", (nickname,)).fetchone()
            if exists:
                error = "That nickname is already taken. Please choose another one."

        if error:
            flash(error, "error")
            return render_template("student_register.html", full_name=full_name, nickname=nickname)

        db = get_db()
        db.execute(
            "INSERT INTO users (full_name, nickname, password_hash, role, created_at) VALUES (?, ?, ?, 'student', ?)",
            (full_name, nickname, generate_password_hash(password), now_iso()),
        )
        db.commit()
        flash("Account created! You can now log in.", "success")
        return redirect(url_for("student_login"))

    return render_template("student_register.html")


@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE nickname = ? AND role = 'student'", (nickname,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for("student_dashboard"))
        flash("Incorrect nickname or password.", "error")
    return render_template("student_login.html")


# --- Discipline Master (DM) setup / login -----------------------------------------

@app.route("/dm/setup", methods=["GET", "POST"])
def dm_setup():
    if dm_exists():
        flash("A Discipline Master account already exists. Please log in.", "notice")
        return redirect(url_for("dm_login"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        nickname = request.form.get("nickname", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        code = request.form.get("setup_code", "")

        error = None
        if code != DM_SETUP_CODE:
            error = "Incorrect setup code. Ask your system administrator for the correct code."
        elif not full_name or not nickname or not password:
            error = "Please fill in every field."
        elif password != confirm:
            error = "Passwords do not match."

        if error:
            flash(error, "error")
            return render_template("dm_setup.html", full_name=full_name, nickname=nickname)

        db = get_db()
        db.execute(
            "INSERT INTO users (full_name, nickname, password_hash, role, created_at) VALUES (?, ?, ?, 'dm', ?)",
            (full_name, nickname, generate_password_hash(password), now_iso()),
        )
        db.commit()
        flash("Discipline Master account created. Please log in.", "success")
        return redirect(url_for("dm_login"))

    return render_template("dm_setup.html")


@app.route("/dm/login", methods=["GET", "POST"])
def dm_login():
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE nickname = ? AND role IN ('dm','viewer')", (nickname,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            if user["role"] == "viewer":
                return redirect(url_for("viewer_results"))
            return redirect(url_for("dm_dashboard"))
        flash("Incorrect nickname or password.", "error")
    return render_template("dm_login.html", dm_exists=dm_exists())


# ---------------------------------------------------------------------------
# Student area
# ---------------------------------------------------------------------------

@app.route("/student/dashboard")
@login_required(role="student")
def student_dashboard():
    db = get_db()
    user = current_user()
    phase = phase_status()
    my_application = db.execute(
        "SELECT applications.*, positions.name AS position_name FROM applications "
        "JOIN positions ON positions.id = applications.position_id WHERE student_id = ?",
        (user["id"],),
    ).fetchone()
    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()

    candidates = db.execute(
        "SELECT applications.id, applications.manifesto, applications.position_id, "
        "users.full_name, users.nickname, positions.name AS position_name "
        "FROM applications JOIN users ON users.id = applications.student_id "
        "JOIN positions ON positions.id = applications.position_id "
        "WHERE applications.status = 'candidate' ORDER BY positions.name"
    ).fetchall()

    my_votes = {
        row["position_id"]: row["application_id"]
        for row in db.execute("SELECT position_id, application_id FROM votes WHERE student_id = ?", (user["id"],))
    }

    winners = db.execute(
        "SELECT positions.name AS position_name, users.full_name, users.nickname "
        "FROM positions JOIN applications ON applications.id = positions.winner_application_id "
        "JOIN users ON users.id = applications.student_id "
        "WHERE positions.winner_application_id IS NOT NULL"
    ).fetchall()

    return render_template(
        "student_dashboard.html",
        phase=phase,
        my_application=my_application,
        positions=positions,
        candidates=candidates,
        my_votes=my_votes,
        winners=winners,
    )


@app.route("/student/apply", methods=["GET", "POST"])
@login_required(role="student")
def student_apply():
    db = get_db()
    user = current_user()
    phase = phase_status()
    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()

    already = db.execute("SELECT 1 FROM applications WHERE student_id = ?", (user["id"],)).fetchone()

    if request.method == "POST":
        if already:
            flash("You have already submitted an application.", "error")
            return redirect(url_for("student_dashboard"))
        if not phase["applications_open"]:
            flash("The application window is not currently open.", "error")
            return redirect(url_for("student_dashboard"))

        position_id = request.form.get("position_id")
        manifesto = request.form.get("manifesto", "").strip()
        file = request.files.get("letter")

        error = None
        if not position_id:
            error = "Please choose the position you are applying for."
        elif not file or file.filename == "":
            error = "Please upload your application letter (Word document)."
        elif not allowed_file(file.filename):
            error = "Only .doc, .docx, or .pdf files are accepted."

        if error:
            flash(error, "error")
            return render_template("apply.html", positions=positions, phase=phase)

        filename = secure_filename(file.filename)
        stored_name = f"{user['id']}_{int(datetime.now().timestamp())}_{filename}"
        file.save(os.path.join(UPLOAD_DIR, stored_name))

        try:
            db.execute(
                "INSERT INTO applications (student_id, position_id, manifesto, file_path, file_name, submitted_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (user["id"], position_id, manifesto, stored_name, filename, now_iso()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("You have already submitted an application.", "error")
            return redirect(url_for("student_dashboard"))

        flash("Your application letter has been submitted. Good luck!", "success")
        return redirect(url_for("student_dashboard"))

    if already:
        flash("You have already submitted an application.", "notice")
        return redirect(url_for("student_dashboard"))
    if not phase["applications_open"]:
        flash("The application window is not currently open.", "notice")
        return redirect(url_for("student_dashboard"))

    return render_template("apply.html", positions=positions, phase=phase)


@app.route("/student/vote", methods=["GET", "POST"])
@login_required(role="student")
def student_vote():
    db = get_db()
    user = current_user()
    phase = phase_status()

    if not phase["voting_open"]:
        flash("Voting is not currently open.", "notice")
        return redirect(url_for("student_dashboard"))

    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()
    candidates_by_position = {}
    for pos in positions:
        cands = db.execute(
            "SELECT applications.id, users.full_name, users.nickname, applications.manifesto "
            "FROM applications JOIN users ON users.id = applications.student_id "
            "WHERE applications.position_id = ? AND applications.status = 'candidate'",
            (pos["id"],),
        ).fetchall()
        candidates_by_position[pos["id"]] = cands

    my_votes = {
        row["position_id"]: row["application_id"]
        for row in db.execute("SELECT position_id, application_id FROM votes WHERE student_id = ?", (user["id"],))
    }

    if request.method == "POST":
        cast = 0
        for pos in positions:
            if pos["id"] in my_votes:
                continue
            field = f"position_{pos['id']}"
            choice = request.form.get(field)
            if choice:
                try:
                    db.execute(
                        "INSERT INTO votes (student_id, position_id, application_id, voted_at) VALUES (?, ?, ?, ?)",
                        (user["id"], pos["id"], choice, now_iso()),
                    )
                    cast += 1
                except sqlite3.IntegrityError:
                    pass
        db.commit()
        if cast:
            flash(f"Thank you! Your vote{'s' if cast != 1 else ''} for {cast} position(s) has been recorded.", "success")
        else:
            flash("No new votes were recorded — you may have already voted.", "notice")
        return redirect(url_for("student_dashboard"))

    return render_template(
        "vote.html", positions=positions, candidates_by_position=candidates_by_position, my_votes=my_votes, phase=phase
    )


# ---------------------------------------------------------------------------
# DM (admin) area
# ---------------------------------------------------------------------------

@app.route("/dm/dashboard")
@login_required(role="dm")
def dm_dashboard():
    db = get_db()
    counts = {
        "students": db.execute("SELECT COUNT(*) c FROM users WHERE role='student'").fetchone()["c"],
        "applications": db.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"],
        "pending": db.execute("SELECT COUNT(*) c FROM applications WHERE status='pending'").fetchone()["c"],
        "candidates": db.execute("SELECT COUNT(*) c FROM applications WHERE status='candidate'").fetchone()["c"],
        "votes": db.execute("SELECT COUNT(*) c FROM votes").fetchone()["c"],
        "positions": db.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"],
    }
    phase = phase_status()
    return render_template("dm_dashboard.html", counts=counts, phase=phase)


@app.route("/dm/settings", methods=["GET", "POST"])
@login_required(role="dm")
def dm_settings():
    db = get_db()
    if request.method == "POST":
        form = request.form
        db.execute(
            "UPDATE config SET application_start=?, application_end=?, campaign_start=?, "
            "voting_start=?, voting_end=?, school_name=? WHERE id=1",
            (
                form.get("application_start") or None,
                form.get("application_end") or None,
                form.get("campaign_start") or None,
                form.get("voting_start") or None,
                form.get("voting_end") or None,
                form.get("school_name") or "Nuvision High School",
            ),
        )
        db.commit()
        flash("Election schedule updated.", "success")
        return redirect(url_for("dm_settings"))

    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()
    return render_template("dm_settings.html", positions=positions)


@app.route("/dm/positions/add", methods=["POST"])
@login_required(role="dm")
def dm_add_position():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Position name cannot be empty.", "error")
    else:
        try:
            get_db().execute("INSERT INTO positions (name) VALUES (?)", (name,))
            get_db().commit()
            flash(f"Position '{name}' added.", "success")
        except sqlite3.IntegrityError:
            flash("That position already exists.", "error")
    return redirect(url_for("dm_settings"))


@app.route("/dm/positions/<int:position_id>/delete", methods=["POST"])
@login_required(role="dm")
def dm_delete_position(position_id):
    db = get_db()
    in_use = db.execute("SELECT 1 FROM applications WHERE position_id = ?", (position_id,)).fetchone()
    if in_use:
        flash("Cannot delete a position that already has applications. Remove those first.", "error")
    else:
        db.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        db.commit()
        flash("Position removed.", "success")
    return redirect(url_for("dm_settings"))


@app.route("/dm/applications")
@login_required(role="dm")
def dm_applications():
    db = get_db()
    status_filter = request.args.get("status")
    query = (
        "SELECT applications.*, users.full_name, users.nickname, positions.name AS position_name "
        "FROM applications JOIN users ON users.id = applications.student_id "
        "JOIN positions ON positions.id = applications.position_id "
    )
    params = ()
    if status_filter:
        query += "WHERE applications.status = ? "
        params = (status_filter,)
    query += "ORDER BY applications.submitted_at DESC"
    apps = db.execute(query, params).fetchall()
    return render_template("dm_applications.html", applications=apps, status_filter=status_filter)


@app.route("/dm/applications/<int:app_id>/update", methods=["POST"])
@login_required(role="dm")
def dm_update_application(app_id):
    db = get_db()
    application = db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if not application:
        abort(404)

    new_status = request.form.get("status")
    feedback = request.form.get("feedback", "").strip()
    valid_statuses = {"pending", "interview_approved", "rejected", "candidate", "not_selected"}

    if new_status not in valid_statuses:
        flash("Invalid status.", "error")
        return redirect(url_for("dm_applications"))

    db.execute(
        "UPDATE applications SET status = ?, feedback = ?, updated_at = ? WHERE id = ?",
        (new_status, feedback, now_iso(), app_id),
    )
    db.commit()
    flash("Application updated.", "success")
    return redirect(request.referrer or url_for("dm_applications"))


@app.route("/dm/uploads/<path:filename>")
@login_required(role="dm")
def dm_download_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)


@app.route("/dm/results")
@login_required(role="dm")
def dm_results():
    db = get_db()
    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()
    return render_template("dm_results.html", positions=positions)


@app.route("/dm/announce/<int:position_id>", methods=["POST"])
@login_required(role="dm")
def dm_announce(position_id):
    db = get_db()
    app_id = request.form.get("application_id")
    if not app_id:
        flash("Choose the winning candidate first.", "error")
        return redirect(url_for("dm_results"))
    db.execute(
        "UPDATE positions SET winner_application_id = ?, winner_announced_at = ? WHERE id = ?",
        (app_id, now_iso(), position_id),
    )
    db.commit()
    flash("Winner announced! Students can now see the result.", "success")
    return redirect(url_for("dm_results"))


@app.route("/dm/viewers", methods=["GET", "POST"])
@login_required(role="dm")
def dm_viewers():
    db = get_db()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        nickname = request.form.get("nickname", "").strip()
        password = request.form.get("password", "")
        if not full_name or not nickname or not password:
            flash("Please fill in every field.", "error")
        else:
            exists = db.execute("SELECT 1 FROM users WHERE nickname = ?", (nickname,)).fetchone()
            if exists:
                flash("That nickname is already taken.", "error")
            else:
                db.execute(
                    "INSERT INTO users (full_name, nickname, password_hash, role, created_at) VALUES (?, ?, ?, 'viewer', ?)",
                    (full_name, nickname, generate_password_hash(password), now_iso()),
                )
                db.commit()
                flash(f"Results-viewer access granted to {full_name}.", "success")
        return redirect(url_for("dm_viewers"))

    viewers = db.execute("SELECT * FROM users WHERE role = 'viewer' ORDER BY created_at DESC").fetchall()
    return render_template("dm_viewers.html", viewers=viewers)


@app.route("/dm/viewers/<int:user_id>/delete", methods=["POST"])
@login_required(role="dm")
def dm_delete_viewer(user_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ? AND role = 'viewer'", (user_id,))
    db.commit()
    flash("Access revoked.", "success")
    return redirect(url_for("dm_viewers"))


# ---------------------------------------------------------------------------
# Read-only viewer area (granted by DM)
# ---------------------------------------------------------------------------

@app.route("/viewer/results")
@login_required(role="viewer")
def viewer_results():
    db = get_db()
    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()
    return render_template("viewer_results.html", positions=positions)


# ---------------------------------------------------------------------------
# JSON API for live-updating results (polled by the browser)
# ---------------------------------------------------------------------------

@app.route("/api/results")
def api_results():
    user = current_user()
    if not user or user["role"] not in ("dm", "viewer"):
        abort(403)
    db = get_db()
    positions = db.execute("SELECT * FROM positions ORDER BY name").fetchall()
    data = []
    for pos in positions:
        candidates = db.execute(
            "SELECT applications.id AS application_id, users.full_name, users.nickname, "
            "COUNT(votes.id) AS vote_count "
            "FROM applications "
            "JOIN users ON users.id = applications.student_id "
            "LEFT JOIN votes ON votes.application_id = applications.id "
            "WHERE applications.position_id = ? AND applications.status = 'candidate' "
            "GROUP BY applications.id ORDER BY vote_count DESC",
            (pos["id"],),
        ).fetchall()
        total = sum(c["vote_count"] for c in candidates) or 0
        data.append({
            "position_id": pos["id"],
            "position_name": pos["name"],
            "total_votes": total,
            "winner_application_id": pos["winner_application_id"],
            "candidates": [
                {
                    "application_id": c["application_id"],
                    "name": c["full_name"],
                    "nickname": c["nickname"],
                    "votes": c["vote_count"],
                    "pct": round((c["vote_count"] / total) * 100, 1) if total else 0,
                }
                for c in candidates
            ],
        })
    return jsonify({"positions": data, "server_time": now_iso()})


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
else:
    init_db()
import os
import random
import string
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pandas as pd

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['UPLOAD_FOLDER'] = 'uploads'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'dm_login'

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'dm' or 'student'
    raw_password = db.Column(db.String(50), nullable=True) # Easy password visible to DM

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    student = db.relationship('User', backref='applications')
    filename = db.Column(db.String(200), nullable=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='Pending') # Pending, Accepted for Interview, Winner

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Helper to generate easy passwords (e.g. 4-digit numbers or simple words)
def generate_easy_password():
    return str(random.randint(1000, 9999))

# DM Upload Excel & Batch Generate Student Credentials
@app.route('/dm/upload-students', methods=['POST'])
@login_required
def upload_students():
    if current_user.role != 'dm':
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file and file.filename.endswith('.xlsx'):
        df = pd.read_excel(file)
        # Assumes Excel column header is 'Name'
        credentials = []
        
        for name in df['Name']:
            clean_name = str(name).strip()
            nickname = clean_name.lower().replace(" ", "_")
            easy_pass = generate_easy_password()
            
            # Check if user exists
            existing_user = User.query.filter_by(username=nickname).first()
            if not existing_user:
                new_user = User(
                    username=nickname,
                    password_hash=generate_password_hash(easy_pass),
                    role='student',
                    raw_password=easy_pass
                )
                db.session.add(new_user)
                credentials.append({'Full Name': clean_name, 'Nickname': nickname, 'Password': easy_pass})
        
        db.session.commit()
        
        # Export Generated Credentials to Excel for DM download
        out_df = pd.DataFrame(credentials)
        out_path = os.path.join(app.config['UPLOAD_FOLDER'], 'generated_credentials.xlsx')
        out_df.to_excel(out_path, index=False)
        return send_file(out_path, as_attachment=True)

    flash("Please upload a valid .xlsx file")
    return redirect(url_for('dm_dashboard'))

# Student Document Application Upload
@app.route('/apply', methods=['POST'])
@login_required
def apply():
    settings = Settings.query.first()
    now = datetime.utcnow()
    
    # Verify Application Window Date
    if settings and settings.start_date and settings.end_date:
        if not (settings.start_date <= now <= settings.end_date):
            flash("Applications are currently closed.")
            return redirect(url_for('student_dashboard'))
            
    file = request.files.get('application_letter')
    if file and file.filename.endswith('.docx'):
        filename = secure_filename(f"{current_user.username}_{file.filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        app_entry = Application(student_id=current_user.id, filename=filename, upload_time=datetime.utcnow())
        db.session.add(app_entry)
        db.session.commit()
        flash("Application submitted successfully!")
    else:
        flash("Only .docx files are permitted.")
        
    return redirect(url_for('student_dashboard'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
    @app.route('/')
def index():
    return render_template('index.html')  # or redirect(url_for('dm_login'))