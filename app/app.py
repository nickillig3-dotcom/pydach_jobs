# ---- Imports (deine bleiben bestehen; wichtig ist Response & PIL falls genutzt) ----
from flask import Flask, render_template, request, redirect, url_for, send_file, abort, flash, Response, session, g
from datetime import datetime, timedelta, date
from io import BytesIO
import os, re, random, string, unicodedata, textwrap
from PIL import Image, ImageDraw, ImageFont
from urllib.parse import quote
import csv
from io import StringIO
from .config import SITE_NAME, OWNER_NAME, IBAN, BIC, PRICE_EUR_A, PRICE_EUR_B, FEATURE_DAYS, FEATURE_GRACE_HOURS, ADMIN_TOKEN
from .db import db, init_db
from .payment import make_epc_qr_png
# PDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")
def _client_ip() -> str:
    # hinter Proxy/Render/… nimmt er X-Forwarded-For, sonst remote_addr
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()

@app.context_processor
def inject_site_name():
    # SITE_NAME aus der Config global in allen Templates verfügbar machen
    return dict(SITE_NAME=SITE_NAME)


def _load_font(size: int):
    # robuste Font-Suche (Windows/Linux/macOS), sonst Default
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

@app.get("/job/<int:job_id>/og.png")
def job_og_image(job_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        job = cur.fetchone()
        if not job:
            abort(404)

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), (7, 35, 72))
    draw = ImageDraw.Draw(img)

    # einfacher Verlauf
    for i in range(H):
        c = 35 + int(50 * i / H)
        draw.line([(0, i), (W, i)], fill=(7, c, 130))

    # Texte
    title_font = _load_font(64)
    body_font  = _load_font(32)
    small_font = _load_font(24)

    margin = 60
    y = margin

    # Site-Name
    draw.text((margin, y), SITE_NAME, font=small_font, fill=(200, 220, 255))
    y += 50

    # Job-Titel (wrap)
    title = job["title"] or ""
    lines = textwrap.wrap(title, width=22)
    for line in lines[:3]:
        draw.text((margin, y), line, font=title_font, fill=(255, 255, 255))
        y += 72

    # Company / Ort
    meta = f'{job["company"] or ""} — {job["location"] or ""}'.strip(" —")
    draw.text((margin, y+10), meta, font=body_font, fill=(220, 235, 255))

    # Ausgabe
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return Response(bio.getvalue(), mimetype="image/png")

@app.get("/job/<int:job_id>/apply")
def job_apply(job_id: int):
    # Job laden
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        job = cur.fetchone()
        if not job:
            abort(404)

    email = (job["contact_email"] or "").strip()
    if not email:
        # Kein Kontakt hinterlegt -> zurück zur Detailseite
        return redirect(url_for("job_detail", job_id=job_id))

    # Klick loggen (best effort)
    try:
        from .db import log_click
        log_click(
            job_id,
            "apply",
            _client_ip(),
            request.headers.get("User-Agent", ""),
            request.referrer or "",
        )
    except Exception:
        pass

    # Mailto bauen
    subject = f"Bewerbung: {job['title']}"
    body = (
        f"Hallo {job['company']},\n\n"
        f"ich habe Ihre Anzeige auf {SITE_NAME} gesehen.\n"
        f"Referenz: PYDACH-{job_id}\n\n"
        f"Viele Grüße\n"
    )
    mailto = f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"

    # Leichte HTML-Seite, die das E-Mail-Programm öffnet (und Fallback-Link zeigt)
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Bewerbung öffnen …</title>
<p>Wir öffnen dein E‑Mail‑Programm …</p>
<script>setTimeout(function(){{window.location.href={mailto!r};}}, 80);</script>
<p><a href="{mailto}">Falls nichts passiert, hier klicken.</a></p>
"""
    return Response(html, mimetype="text/html")

@app.context_processor
def inject_active_sponsor():
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    with db() as conn:
        cur = conn.cursor()
        # 1) Aktiver Zeitraum
        cur.execute("""
            SELECT * FROM sponsors
            WHERE status='active'
              AND (starts_at IS NULL OR starts_at <= ?)
              AND (ends_at   IS NULL OR ends_at   >= ?)
            ORDER BY starts_at DESC
            LIMIT 1
        """, (now, now))
        s = cur.fetchone()
        if not s:
            # 2) Fallback: zugehörige Order ist bezahlt -> auch zeigen
            cur.execute("""
              SELECT s.* FROM sponsors s
              JOIN orders o ON o.id = s.order_id
              WHERE (s.status='paid' OR s.status='pending')
                AND o.status='paid'
              ORDER BY s.created_at DESC
              LIMIT 1
            """)
            s = cur.fetchone()
    return dict(active_sponsor=s)


init_db()

def now():
    return datetime.utcnow()

def order_reference(order_id: int) -> str:
    rand = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PYDACH-{order_id:05d}-{rand}"

# --- A/B Preis Steuerung ---
def current_ab_group():
    ab = request.cookies.get("ab")
    if ab in ("A","B"):
        return ab, False
    # neu zuweisen
    ab = random.choice(("A","B"))
    g._set_ab_cookie = ab
    return ab, True

def current_price_eur():
    ab, _ = current_ab_group()
    return PRICE_EUR_A if ab == "A" else PRICE_EUR_B

@app.after_request
def persist_ab_cookie(resp):
    if hasattr(g, "_set_ab_cookie") and g._set_ab_cookie in ("A","B"):
        resp.set_cookie("ab", g._set_ab_cookie, max_age=60*60*24*90, samesite="Lax")
    return resp

# --- Sponsoring Helper ---
def active_sponsor():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM sponsors
            WHERE status='paid'
              AND (starts_at IS NULL OR datetime(starts_at) <= datetime('now'))
              AND (ends_at IS NULL OR datetime(ends_at) >= datetime('now'))
            ORDER BY created_at DESC LIMIT 1
        """)
        return cur.fetchone()

@app.context_processor
def inject_globals():
    ab, _ = current_ab_group()
    return dict(SITE_NAME=SITE_NAME, price_eur=current_price_eur(), ab_group=ab, current_sponsor=active_sponsor())

# --- Housekeeping ---
@app.before_request
def housekeeping():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE jobs
            SET grace_expires_at = NULL
            WHERE grace_expires_at IS NOT NULL
              AND datetime(grace_expires_at) <= datetime('now')
        """)
        cur.execute(f"""
            UPDATE jobs
            SET is_featured = 0
            WHERE is_featured = 1
              AND NOT EXISTS (
                SELECT 1 FROM orders o
                WHERE o.job_id = jobs.id
                  AND o.status = 'paid'
                  AND o.paid_at > datetime('now', '-{FEATURE_DAYS} days')
              )
        """)

# --- Marketing Helpers ---
def slugify(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s).strip('-').lower()
    return s

SKILLS = {
    "django": ["django"],
    "flask": ["flask"],
    "fastapi": ["fastapi"],
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "sklearn": ["scikit-learn", "sklearn"],
    "pytorch": ["pytorch", " torch"],
    "tensorflow": ["tensorflow"],
    "spark": ["spark", "pyspark"],
    "airflow": ["airflow"],
    "kafka": ["kafka"],
    "kubernetes": ["kubernetes", "k8s"],
    "docker": ["docker"],
    "aws": ["aws"],
    "azure": ["azure"],
    "gcp": ["gcp", "google cloud"],
    "sql": [" sql", "sql "],
    "etl": ["etl"],
    "mlops": ["mlops"],
    "nlp": ["nlp", "natural language processing"]
}
SKILL_LABEL = { "django":"Django","flask":"Flask","fastapi":"FastAPI","pandas":"Pandas","numpy":"NumPy","sklearn":"scikit-learn",
                "pytorch":"PyTorch","tensorflow":"TensorFlow","spark":"Apache Spark","airflow":"Apache Airflow","kafka":"Apache Kafka",
                "kubernetes":"Kubernetes","docker":"Docker","aws":"AWS","azure":"Azure","gcp":"Google Cloud (GCP)","sql":"SQL","etl":"ETL","mlops":"MLOps","nlp":"NLP"}

def collect_jobs():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE status='published' ORDER BY created_at DESC")
        return cur.fetchall()

CITY_STOP = {"de","ch","at","dach","remote","homeoffice","hybrid","gmbh","ag"}

def valid_city_token(raw: str) -> bool:
    if not raw: return False
    s = raw.strip()
    if len(s) < 3: return False
    t = s.lower()

    # Nur Buchstaben/Leer/Bindestrich (inkl. Umlaute/ß)
    if not re.fullmatch(r"[a-zäöüß \-]+", t): 
        return False

    # mindestens ein Vokal
    if not re.search(r"[aeiouäöü]", t):
        return False

    # Stopwörter und Offensichtliches
    if t in CITY_STOP: 
        return False
    if t in {"penis","fuck","shit"}:
        return False

    # Verhältnis unterschiedliche Buchstaben / Gesamtlänge >= 0.5
    letters = [ch for ch in t if ch.isalpha()]
    if not letters:
        return False
    uniq_ratio = len(set(letters)) / len(letters)
    if uniq_ratio < 0.5:
        return False

    # Wiederholungsmuster (2er oder 3er N-Gramme)
    if re.fullmatch(r"(..)\1{2,}", t) or re.fullmatch(r"(...)\1{1,}", t):
        return False

    return True

def location_variants(loc: str):
    if not loc:
        return []
    parts = re.split(r"[,\-/|–—]+", loc)
    out = []
    for p in parts:
        if valid_city_token(p):
            s = slugify(p.strip())
            if s and s not in out:
                out.append(s)
    return out


def job_skills(text: str):
    text = (text or "").lower()
    hits = set()
    for slug, alts in SKILLS.items():
        if any(a in text for a in alts):
            hits.add(slug)
    return hits

def featured_or_grace(job) -> bool:
    gf = False
    grace = job.get("grace_expires_at")
    if grace:
        try:
            gf = now() < datetime.fromisoformat(grace)
        except Exception:
            pass
    return job.get("is_featured", 0) == 1 or gf

# --- Seiten ---
@app.get("/")
def index():
    q = request.args.get("q", "").strip().lower()
    loc = request.args.get("loc", "").strip().lower()
    jobs = collect_jobs()

    def match(j):
        ok = True
        if q:
            ok = q in j["title"].lower() or q in j["company"].lower() or q in (j["description"] or "").lower()
        if ok and loc:
            ok = loc in (j.get("location","") or "").lower()
        return ok

    jobs = [j for j in jobs if match(j)]
    jobs.sort(key=lambda j: (not featured_or_grace(j), j["created_at"]), reverse=False)

    # Cities
    city_counts = {}
    for j in jobs:
        for ls in location_variants(j.get("location","")):
            if ls:
                city_counts[ls] = city_counts.get(ls, 0) + 1
    city_name = {}
    for j in jobs:
        loc_label = j.get("location") or ""
        for part in re.split(r"[,\-/|–—]+", loc_label):
            s = slugify(part)
            if s and s not in city_name:
                city_name[s] = part.strip().title()
    top_cities = sorted([(s, city_name.get(s, s.title()), c) for s,c in city_counts.items()], key=lambda x: x[2], reverse=True)[:12]

    # Skills
    skill_counts = {}
    for j in jobs:
        txt = f"{j['title']} {j.get('description','')}"
        for s in job_skills(txt):
            skill_counts[s] = skill_counts.get(s, 0) + 1
    top_skills = sorted([(s, SKILL_LABEL.get(s, s.title()), c) for s,c in skill_counts.items()], key=lambda x: x[2], reverse=True)[:12]

    # ✅ Meta-Infos (fixiert)
    meta_title = f"{SITE_NAME} — Aktuelle Python-Jobs (DACH)"
    meta_desc  = "Aktuelle Python-Jobs in Deutschland, Österreich und der Schweiz. Python, Django, FastAPI, Data, ML — stöbern & bewerben."
    meta_img   = url_for("static", filename="og.png", _external=True)

    # ✅ Render: Meta-Parameter nur einmal übergeben
    return render_template(
        "index.html",
        jobs=jobs,
        top_cities=top_cities,
        top_skills=top_skills,
        meta_title=meta_title,
        meta_desc=meta_desc,
        meta_img=meta_img,
    )



# --- Anti-Spam helpers ---
def is_bot_post(form_key_prefix: str) -> bool:
    # Honeypot
    if request.form.get("homepage"):
        return True
    # Captcha
    ans = request.form.get("captcha","").strip()
    key = f"captcha_{form_key_prefix}"
    try:
        target = int(session.get(key, "-999"))
        given = int(ans)
    except Exception:
        return True
    return given != target

@app.route("/jobs/new", methods=["GET", "POST"])
def post_job():
    if request.method == "POST":
        if is_bot_post("job"):
            flash("Sicherheitsprüfung fehlgeschlagen. Bitte erneut versuchen.", "error")
            # neue Aufgabe erzeugen und Formular erneut rendern
            a,b = random.randint(1,9), random.randint(1,9)
            session["captcha_job"] = a + b
            return render_template("post_job.html", cap_a=a, cap_b=b)
        title = request.form.get("title","").strip()
        company = request.form.get("company","").strip()
        location = request.form.get("location","").strip()
        email = request.form.get("email","").strip()
        logo_url = request.form.get("logo_url","").strip()
        description = request.form.get("description","").strip()
        if not title or not company or not description:
            flash("Titel, Unternehmen und Beschreibung sind Pflichtfelder.", "error")
            a,b = random.randint(1,9), random.randint(1,9)
            session["captcha_job"] = a + b
            return render_template("post_job.html", cap_a=a, cap_b=b)
        grace_until = (now() + timedelta(hours=FEATURE_GRACE_HOURS)).isoformat(sep=" ", timespec="seconds")
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO jobs (title, company, location, email, logo_url, description, grace_expires_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (title, company, location, email, logo_url, description, grace_until))
            job_id = cur.lastrowid
            price_cents = int(round(current_price_eur() * 100))
            ab = current_ab_group()[0]
            cur.execute("""INSERT INTO orders (job_id, price_cents, currency, reference, ab_group)
                           VALUES (?,?,?,?,?)""", (job_id, price_cents, "EUR", "TEMP", ab))
            order_id = cur.lastrowid
            ref = order_reference(order_id)
            cur.execute("UPDATE orders SET reference=? WHERE id=?", (ref, order_id))
        return redirect(url_for("checkout", order_id=order_id))
    # GET → captcha erzeugen
    a,b = random.randint(1,9), random.randint(1,9)
    session["captcha_job"] = a + b
    return render_template("post_job.html", cap_a=a, cap_b=b, meta_title=f"Job einstellen — {SITE_NAME}")

@app.get("/job/<int:job_id>")
def job_detail(job_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        job = cur.fetchone()
    if not job or job["status"] != "published":
        abort(404)
    return render_template("job_detail.html",
                           job=job,
                           featured=featured_or_grace(job),
                           meta_title=f"{job['title']} — {job['company']} | {SITE_NAME}",
                           meta_desc=(job.get('description','')[:160] or f"Job bei {job['company']}"))

@app.get("/checkout/<int:order_id>")
def checkout(order_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        order = cur.fetchone()
        if not order:
            abort(404)
        cur.execute("SELECT * FROM jobs WHERE id=?", (order["job_id"],))
        job = cur.fetchone()
    amount = order["price_cents"] / 100.0
    return render_template("checkout.html",
                           order=order, job=job,
                           amount=amount, iban=IBAN, bic=BIC, owner_name=OWNER_NAME,
                           meta_title=f"Checkout — {SITE_NAME}",
                           meta_desc="Überweisung per EPC‑QR/GiroCode — schnell & ohne Gateway.")

@app.get("/checkout/<int:order_id>/qr.png")
def checkout_qr(order_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        order = cur.fetchone()
    if not order:
        abort(404)
    amount = order["price_cents"] / 100.0
    png = make_epc_qr_png(IBAN, OWNER_NAME, amount, order["reference"], bic=BIC, scale=6)
    return send_file(BytesIO(png), mimetype="image/png", download_name=f"sepa_{order_id}.png")

# --- Admin ---
@app.get("/admin")
def admin():
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)

    with db() as conn:
        cur = conn.cursor()

        # --- Bewerben-Klicks (KPIs) ---
        cur.execute("SELECT COUNT(*) AS n FROM clicks WHERE kind='apply'")
        apply_total = (cur.fetchone() or {}).get("n", 0)

        cur.execute("""
            SELECT j.id, j.title, COUNT(c.id) AS n
            FROM jobs j
            JOIN clicks c ON c.job_id = j.id AND c.kind='apply'
            GROUP BY j.id
            ORDER BY n DESC
            LIMIT 20
        """)
        apply_by_job = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) AS n7
            FROM clicks
            WHERE kind='apply' AND created_at >= datetime('now', '-7 days')
        """)
        apply_7d = (cur.fetchone() or {}).get("n7", 0)

        # --- Bestehende Admin-Auswertung ---
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200")
        orders = cur.fetchall()

        jobs = {}
        ids = [o["job_id"] for o in orders if o["job_id"] != 0]
        if ids:
            cur.execute(f"SELECT * FROM jobs WHERE id IN ({','.join(['?']*len(ids))})", tuple(set(ids)))
            jobs = {j["id"]: j for j in cur.fetchall()}

        # A/B-Report
        cur.execute("""
            SELECT COALESCE(ab_group,'—') AS ab,
                   COUNT(*) AS orders,
                   SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) AS paid,
                   SUM(CASE WHEN status='paid' THEN price_cents ELSE 0 END) AS revenue_cents
            FROM orders GROUP BY COALESCE(ab_group,'—') ORDER BY ab
        """)
        ab_report = []
        for r in cur.fetchall():
            o = r["orders"] or 0
            p = r["paid"] or 0
            ab_report.append(dict(
                ab=r["ab"], orders=o, paid=p,
                conv=round((p/o*100.0) if o else 0.0, 1),
                revenue_eur=(r["revenue_cents"] or 0)/100.0
            ))

        # KPIs gesamt & 7 Tage
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat(sep=" ", timespec="seconds")
        cur.execute("SELECT COUNT(*) AS c FROM orders")
        total_orders = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM orders WHERE status='paid'")
        paid_orders = cur.fetchone()["c"]
        cur.execute("SELECT SUM(price_cents) AS s FROM orders WHERE status='paid'")
        revenue_total = (cur.fetchone()["s"] or 0)/100.0
        cur.execute("SELECT COUNT(*) AS c FROM orders WHERE created_at >= ?", (week_ago,))
        orders_7d = cur.fetchone()["c"]
        cur.execute("SELECT SUM(price_cents) AS s FROM orders WHERE status='paid' AND paid_at >= ?", (week_ago,))
        revenue_7d = (cur.fetchone()["s"] or 0)/100.0
        kpis = dict(
            revenue_total=revenue_total,
            revenue_7d=revenue_7d,
            orders_total=total_orders,
            orders_7d=orders_7d,
            conv_total=round((paid_orders/total_orders*100.0) if total_orders else 0.0, 1),
        )

    for o in orders:
        o["job"] = jobs.get(o["job_id"])
        o["amount"] = o["price_cents"]/100.0

    with db() as conn2:
        cur2 = conn2.cursor()
        cur2.execute("SELECT * FROM sponsors ORDER BY created_at DESC LIMIT 100")
        sponsors = cur2.fetchall()

    return render_template(
        "admin.html",
        orders=orders,
        sponsors=sponsors,
        ab_report=ab_report,
        kpis=kpis,
        apply_total=apply_total,
        apply_7d=apply_7d,
        apply_by_job=apply_by_job,
        token=token,
        meta_title=f"Admin — {SITE_NAME}",
    )

@app.post("/admin/order/<int:order_id>/mark_paid")
def admin_order_mark_paid(order_id: int):
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    with db() as conn:
        cur = conn.cursor()
        # Order auf paid setzen
        cur.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=?", (now, order_id))
        # Wenn es ein Sponsor-Order ist (job_id == 0) -> Sponsor aktivieren
        cur.execute("SELECT job_id FROM orders WHERE id=?", (order_id,))
        o = cur.fetchone()
        if o and o["job_id"] == 0:
            cur.execute("SELECT * FROM sponsors WHERE order_id=?", (order_id,))
            s = cur.fetchone()
            if s:
                ends = (datetime.utcnow() + timedelta(days=7)).isoformat(sep=" ", timespec="seconds")
                cur.execute("UPDATE sponsors SET status='active', starts_at=?, ends_at=? WHERE id=?", (now, ends, s["id"]))
    return redirect(url_for("admin", token=token))

@app.post("/admin/order/<int:order_id>/mark_unpaid")
def mark_unpaid(order_id: int):
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE orders SET status='pending', paid_at=NULL WHERE id=?", (order_id,))
        cur.execute("SELECT job_id FROM orders WHERE id=?", (order_id,))
        job_id = cur.fetchone()["job_id"]
        if job_id != 0:
            cur.execute("UPDATE jobs SET is_featured=0 WHERE id=?", (job_id,))
    return redirect(url_for("admin", token=token))

@app.post("/admin/import")
def import_csv():
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)
    file = request.files.get("file")
    if not file:
        flash("Keine CSV hochgeladen.", "error")
        return redirect(url_for("admin", token=token))
    text = file.read().decode("utf-8", errors="ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    headers = [h.strip().lower() for h in lines[0].split(",")]
    purpose_idx = None
    for i,h in enumerate(headers):
        if "verwendungszweck" in h or "purpose" in h or "reference" in h or "ref" in h:
            purpose_idx = i
    if purpose_idx is None:
        flash("Konnte Spalte mit Verwendungszweck/Reference nicht erkennen.", "error")
        return redirect(url_for("admin", token=token))
    imported = 0
    updated = 0
    with db() as conn:
        cur = conn.cursor()
        for ln in lines[1:]:
            cols = [c.strip() for c in ln.split(",")]
            if purpose_idx >= len(cols):
                continue
            purpose = cols[purpose_idx]
            ref = None
            for part in purpose.replace(";", " ").split():
                if part.upper().startswith("PYDACH-"):
                    ref = part.upper().strip(".,;")
                    break
            if not ref:
                continue
            cur.execute("SELECT * FROM orders WHERE reference=?", (ref,))
            o = cur.fetchone()
            if not o:
                continue
            if o["status"] != "paid":
                cur.execute("UPDATE orders SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=?", (o["id"],))
                if o["job_id"] != 0:
                    cur.execute("UPDATE jobs SET is_featured=1 WHERE id=?", (o["job_id"],))
                else:
                    cur.execute("UPDATE sponsors SET status='paid' WHERE order_id=?", (o["id"],))
                updated += 1
            imported += 1
    flash(f"Import fertig. Einträge geprüft: {imported}, neu bezahlt: {updated}.", "success")
    return redirect(url_for("admin", token=token))

@app.get("/admin/social")
def admin_social():
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)

    since = (datetime.utcnow() - timedelta(days=7)).isoformat(sep=" ", timespec="seconds")
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, company, location
              FROM jobs
             WHERE created_at >= ?
             ORDER BY created_at DESC
             LIMIT 8
        """, (since,))
        jobs = cur.fetchall()

    # LinkedIn-Text (mehrere Zeilen)
    lines = [f"Top Python‑Jobs (letzte 7 Tage) — {SITE_NAME}"]
    for j in jobs[:6]:
        url = url_for("job_detail", job_id=j["id"], _external=True)
        lines.append(f"• {j['title']} — {j['company']} ({j['location']}) {url}")
    linkedin = "\n".join(lines) + "\n\n#pythonjobs #dach #flask"

    # Twitter/X (280 Zeichen Budget)
    tw = "Neue Python‑Jobs: "
    for j in jobs[:4]:
        url = url_for("job_detail", job_id=j["id"], _external=True)
        piece = f"{j['title']} ({j['location']}) {url} • "
        if len(tw) + len(piece) <= 270:
            tw += piece
    tw = tw.rstrip(" •") + " #pythonjobs"

    return render_template("admin_social.html", linkedin=linkedin, twitter=tw,
                           token=token, meta_title=f"Social‑Teaser — {SITE_NAME}")

@app.get("/admin/clicks.csv")
def admin_clicks_csv():
    if request.args.get("token") != ADMIN_TOKEN:
        abort(401)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT created_at, job_id, kind, ip, ref, ua
            FROM clicks
            ORDER BY created_at DESC
            LIMIT 10000
        """)
        rows = cur.fetchall()
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(["created_at", "job_id", "kind", "ip", "ref", "ua"])
    for r in rows:
        w.writerow([
            r["created_at"], r["job_id"], r["kind"],
            (r["ip"] or "")[:64],
            (r["ref"] or "")[:256],
            (r["ua"] or "")[:256],
        ])
    return Response(
        sio.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=clicks.csv"}
    )

# --- Sponsoring ---
from urllib.parse import urlparse

@app.route("/sponsor/new", methods=["GET", "POST"])
def sponsor_new():
    if request.method == "POST":
        if is_bot_post("sponsor"):
            flash("Sicherheitsprüfung fehlgeschlagen. Bitte erneut versuchen.", "error")
            a,b = random.randint(1,9), random.randint(1,9)
            session["captcha_sponsor"] = a + b
            return render_template("sponsor_new.html", cap_a=a, cap_b=b)
        company = request.form.get("company","").strip()
        website = request.form.get("website","").strip()
        banner_text = request.form.get("banner_text","").strip()
        image_url = request.form.get("image_url","").strip()  # NEU
        if not company or not banner_text:
            flash("Firma und Banner‑Text sind Pflicht.", "error")
            a,b = random.randint(1,9), random.randint(1,9)
            session["captcha_sponsor"] = a + b
            return render_template("sponsor_new.html", cap_a=a, cap_b=b)
        if website and not urlparse(website).scheme:
            website = "https://" + website
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO sponsors (company, website, banner_text, image_url, status)
                   VALUES (?,?,?,?, 'pending')""",
                (company, website, banner_text, image_url)
            )
            sponsor_id = cur.lastrowid
            price_cents = int(round(current_price_eur() * 100))  # hier gleicher AB-Preis; kann separat gemacht werden
            ab = current_ab_group()[0]
            cur.execute("""INSERT INTO orders (job_id, price_cents, currency, reference, ab_group)
                           VALUES (?,?,?,?,?)""", (0, price_cents, "EUR", "TEMP", ab))
            order_id = cur.lastrowid
            ref = order_reference(order_id)
            cur.execute("UPDATE orders SET reference=? WHERE id=?", (ref, order_id))
            cur.execute("UPDATE sponsors SET order_id=? WHERE id=?", (order_id, sponsor_id))
        return redirect(url_for("sponsor_checkout", sponsor_id=sponsor_id))
    a,b = random.randint(1,9), random.randint(1,9)
    session["captcha_sponsor"] = a + b
    return render_template("sponsor_new.html", cap_a=a, cap_b=b, meta_title=f"Sponsor werden — {SITE_NAME}")

@app.get("/sponsor/checkout/<int:sponsor_id>")
def sponsor_checkout(sponsor_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM sponsors WHERE id=?", (sponsor_id,))
        sp = cur.fetchone()
        if not sp:
            abort(404)
        cur.execute("SELECT * FROM orders WHERE id=?", (sp["order_id"],))
        order = cur.fetchone()
    amount = order["price_cents"]/100.0
    return render_template("sponsor_checkout.html",
                           sp=sp, order=order, amount=amount, iban=IBAN, bic=BIC, owner_name=OWNER_NAME,
                           meta_title=f"Sponsoring — {SITE_NAME}")

@app.post("/admin/sponsor/<int:sponsor_id>/mark_paid")
def sponsor_mark_paid(sponsor_id: int):
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)
    with db() as conn:
        cur = conn.cursor()
        # Bestellung als bezahlt
        cur.execute("SELECT order_id FROM sponsors WHERE id=?", (sponsor_id,))
        order_id = cur.fetchone()["order_id"]
        cur.execute("UPDATE orders SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
        # Sponsor aktivieren
        cur.execute("UPDATE sponsors SET status='paid' WHERE id=?", (sponsor_id,))
    return redirect(url_for("admin", token=token))

# --- robots/sitemap/feed/landing/weekly (wie zuvor) ---
@app.get("/robots.txt")
def robots_txt():
    return Response(
        f"User-agent: *\nAllow: /\nSitemap: {url_for('sitemap_xml', _external=True)}\n",
        mimetype="text/plain"
    )

@app.get("/sitemap.xml")
def sitemap_xml():
    jobs = collect_jobs()
    urls = []
    urls.append(f"<url><loc>{url_for('index', _external=True)}</loc><changefreq>daily</changefreq></url>")
    urls.append(f"<url><loc>{url_for('post_job', _external=True)}</loc><changefreq>monthly</changefreq></url>")
    for j in jobs[:500]:
        urls.append(f"<url><loc>{url_for('job_detail', job_id=j['id'], _external=True)}</loc><changefreq>weekly</changefreq></url>")

    # Städte & Skills
    city_counts = {}
    for j in jobs:
        for ls in location_variants(j.get("location","")):
            if ls:
                city_counts[ls] = city_counts.get(ls, 0) + 1
    for slug, _cnt in list(sorted(city_counts.items(), key=lambda x: x[1], reverse=True))[:50]:
        urls.append(f"<url><loc>{url_for('city_page', city_slug=slug, _external=True)}</loc><changefreq>weekly</changefreq></url>")

    skill_counts = {}
    for j in jobs:
        txt = f"{j['title']} {j.get('description','')}"
        for s in job_skills(txt):
            skill_counts[s] = skill_counts.get(s, 0) + 1
    for slug, _cnt in list(sorted(skill_counts.items(), key=lambda x: x[1], reverse=True))[:50]:
        urls.append(f"<url><loc>{url_for('skill_page', skill_slug=slug, _external=True)}</loc><changefreq>weekly</changefreq></url>")

    # Kombinationen (Top 50 reale Paare)
    combo_counts = {}
    for j in jobs:
        cities = location_variants(j.get("location",""))
        skills = job_skills(f"{j['title']} {j.get('description','')}")
        for c in cities:
            for s in skills:
                combo_counts[(c,s)] = combo_counts.get((c,s), 0) + 1
    for (c,s),cnt in list(sorted(combo_counts.items(), key=lambda x: x[1], reverse=True))[:50]:
        urls.append(f"<url><loc>{url_for('city_skill_page', city_slug=c, skill_slug=s, _external=True)}</loc><changefreq>weekly</changefreq></url>")

    # Weekly
    today = date.today()
    for k in range(0, 8):
        d = today - timedelta(weeks=k)
        y, w, _ = d.isocalendar()
        urls.append(f"<url><loc>{url_for('weekly_by_id', year=y, week=w, _external=True)}</loc><changefreq>weekly</changefreq></url>")

    xml = "<?xml version='1.0' encoding='UTF-8'?>\n" \
          "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(urls) + "</urlset>"
    return Response(xml, mimetype="application/xml")

@app.get("/feed.xml")
def feed_xml():
    from html import escape
    jobs = collect_jobs()
    items = []
    for j in jobs[:50]:
        link = url_for('job_detail', job_id=j['id'], _external=True)
        title = escape(f"{j['title']} – {j['company']}")
        desc = escape((j.get('description') or "")[:500])
        items.append(
            f"<item><title>{title}</title><link>{link}</link><guid>{link}</guid>"
            f"<description><![CDATA[{desc}]]></description></item>"
        )
    xml = ("<?xml version='1.0' encoding='UTF-8'?>"
           "<rss version='2.0'><channel>"
           f"<title>{SITE_NAME} – Neue Jobs</title>"
           f"<link>{url_for('index', _external=True)}</link>"
           f"<description>Aktuelle Python‑Jobs im DACH‑Raum</description>"
           + "".join(items) +
           "</channel></rss>")
    return Response(xml, mimetype="application/rss+xml")

@app.get("/c/<city_slug>")
def city_page(city_slug: str):
    jobs = collect_jobs()
    sel = []
    display_name = None
    for j in jobs:
        variants = location_variants(j.get("location",""))
        if city_slug in variants:
            sel.append(j)
            if not display_name:
                for part in re.split(r"[,\-/|–—]+", j.get("location","")):
                    if slugify(part) == city_slug:
                        display_name = part.strip().title()
                        break
    sel.sort(key=lambda j: (not featured_or_grace(j), j["created_at"]))

    # Top-Skills in dieser Stadt
    skill_counts = {}
    for j in sel:
        txt = f"{j['title']} {j.get('description','')}"
        for s in job_skills(txt):
            skill_counts[s] = skill_counts.get(s, 0) + 1
    top_skills = sorted([(s, SKILL_LABEL.get(s, s.title()), c) for s,c in skill_counts.items()],
                        key=lambda x: x[2], reverse=True)[:8]

    return render_template("landing_city.html",
                           jobs=sel,
                           city=display_name or city_slug.title(),
                           city_slug=city_slug,
                           top_skills=top_skills,
                           country=None,
                           meta_title=f"Python‑Jobs in {display_name or city_slug.title()} | {SITE_NAME}",
                           meta_desc=f"Aktuelle Python‑Jobs in {display_name or city_slug.title()} (DACH).")

@app.get("/s/<skill_slug>")
def skill_page(skill_slug: str):
    jobs = collect_jobs()
    sel = []
    for j in jobs:
        txt = f"{j['title']} {j.get('description','')}"
        if skill_slug in job_skills(txt):
            sel.append(j)
    sel.sort(key=lambda j: (not featured_or_grace(j), j["created_at"]))
    label = SKILL_LABEL.get(skill_slug, skill_slug.title())

    # Top-Städte für diesen Skill
    city_counts = {}
    city_name = {}
    for j in sel:
        loc_label = j.get("location") or ""
        for part in re.split(r"[,\-/|–—]+", loc_label):
            s = slugify(part)
            if s:
                city_counts[s] = city_counts.get(s, 0) + 1
                city_name.setdefault(s, part.strip().title())
    top_cities = sorted([(s, city_name.get(s, s.title()), c) for s,c in city_counts.items()],
                        key=lambda x: x[2], reverse=True)[:8]

    return render_template("landing_skill.html",
                           jobs=sel,
                           skill_label=label,
                           skill_slug=skill_slug,
                           top_cities=top_cities,
                           meta_title=f"{label}-Jobs (DACH) | {SITE_NAME}",
                           meta_desc=f"Python‑Jobs mit {label} im DACH‑Raum.")
@app.get("/c/<city_slug>/s/<skill_slug>")
def city_skill_page(city_slug: str, skill_slug: str):
    jobs = collect_jobs()
    sel = []
    display_name = None
    for j in jobs:
        variants = location_variants(j.get("location",""))
        if city_slug in variants:
            txt = f"{j['title']} {j.get('description','')}"
            if skill_slug in job_skills(txt):
                sel.append(j)
                if not display_name:
                    for part in re.split(r"[,\-/|–—]+", j.get("location","")):
                        if slugify(part) == city_slug:
                            display_name = part.strip().title()
                            break
    sel.sort(key=lambda j: (not featured_or_grace(j), j["created_at"]))
    label = SKILL_LABEL.get(skill_slug, skill_slug.title())
    return render_template("landing_combo.html",
                           jobs=sel,
                           city=display_name or city_slug.title(),
                           skill_label=label,
                           meta_title=f"{label}‑Jobs in {display_name or city_slug.title()} | {SITE_NAME}",
                           meta_desc=f"Python‑Jobs in {display_name or city_slug.title()} mit {label}.")


@app.get("/weekly")
def weekly_current():
    today = date.today()
    y,w,_ = today.isocalendar()
    return redirect(url_for('weekly_by_id', year=y, week=w))

@app.get("/weekly/<int:year>-<int:week>")
def weekly_by_id(year: int, week: int):
    d = date.fromisocalendar(year, week, 1)  # Montag
    start = datetime(d.year, d.month, d.day, 0, 0, 0)
    end = start + timedelta(days=7)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE status='published' AND datetime(created_at) >= ? AND datetime(created_at) < ? ORDER BY created_at DESC",
                    (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")))
        sel = cur.fetchall()
    return render_template("weekly.html",
                           jobs=sel,
                           year=year, week=week,
                           start=start.strftime("%Y-%m-%d"),
                           end=(end - timedelta(seconds=1)).strftime("%Y-%m-%d"),
                           meta_title=f"Top Python‑Jobs – Woche {week}/{year} | {SITE_NAME}",
                           meta_desc=f"Neue Python‑Jobs im DACH‑Raum in Woche {week}/{year}.")

# --- Rechnung PDF ---
def invoice_pdf_buffer(order, job=None, sponsor=None):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Header
    c.setFont("Helvetica-Bold", 16)
    title = "Rechnung" if order["status"] == "paid" else "Proforma-Rechnung"
    c.drawString(40, height-60, f"{title} — {SITE_NAME}")
    c.setFont("Helvetica", 10)
    c.drawString(40, height-78, f"Rechnungsnr.: INV-{datetime.utcnow().strftime('%Y')}-{order['id']:05d}")
    c.drawString(40, height-92, f"Datum: {datetime.utcnow().strftime('%Y-%m-%d')}")
    c.drawString(40, height-106, f"Referenz: {order['reference']}")

    # Anbieter (wir)
    y = height - 140
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Leistungserbringer")
    c.setFont("Helvetica", 10)
    c.drawString(40, y-16, OWNER_NAME)
    c.drawString(40, y-30, f"IBAN: {IBAN}")
    if BIC:
        c.drawString(40, y-44, f"BIC: {BIC}")

    # Kunde
    y -= 80
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Leistungsempfänger")
    c.setFont("Helvetica", 10)
    if job:
        c.drawString(40, y-16, job.get("company",""))
        if job.get("email"):
            c.drawString(40, y-30, job["email"])
    elif sponsor:
        c.drawString(40, y-16, sponsor.get("company",""))
        if sponsor.get("website"):
            c.drawString(40, y-30, sponsor["website"])
    else:
        c.drawString(40, y-16, "Unbekannt")

    # Positionen
    y -= 70
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Positionen")
    c.setFont("Helvetica", 10)

    item = ""
    if job:
        item = "Featured Job Listing (30 Tage)"
    elif sponsor:
        item = "Sponsoring Top‑Banner (7 Tage)"
    else:
        item = "Leistung"

    amount = order["price_cents"]/100.0
    c.drawString(40, y-18, f"1x {item}")
    c.drawRightString(width-40, y-18, f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))

    # Summe
    y -= 50
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Gesamt")
    c.drawRightString(width-40, y, f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", "."))

    # Fuß
    y -= 40
    c.setFont("Helvetica", 8)
    c.drawString(40, y, "Hinweis: Beispiel-Rechnung. USt-Hinweis bitte an dein Unternehmen anpassen (z. B. §19 UStG / Reverse Charge).")
    if order["status"] != "paid":
        c.setFillColorRGB(0.8, 0.0, 0.0)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y-16, "Unbezahlt — Zahlung per SEPA-Überweisung, Verwendungszweck siehe Checkout.")
        c.setFillColorRGB(0,0,0)

    c.showPage()
    c.save()
    return buf.getvalue()

@app.get("/admin/order/<int:order_id>/invoice.pdf")
def order_invoice_pdf(order_id: int):
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        order = cur.fetchone()
        if not order:
            abort(404)
        job = None
        sponsor = None
        if order["job_id"] != 0:
            cur.execute("SELECT * FROM jobs WHERE id=?", (order["job_id"],))
            job = cur.fetchone()
        else:
            cur.execute("SELECT * FROM sponsors WHERE order_id=?", (order_id,))
            sponsor = cur.fetchone()
    pdf = invoice_pdf_buffer(order, job=job, sponsor=sponsor)
    return send_file(BytesIO(pdf), mimetype="application/pdf", download_name=f"invoice_{order_id}.pdf")
@app.get("/invoice/<int:order_id>.pdf")
def invoice_public(order_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        order = cur.fetchone()
        if not order:
            abort(404)
        job = sponsor = None
        if order["job_id"] != 0:
            cur.execute("SELECT * FROM jobs WHERE id=?", (order["job_id"],))
            job = cur.fetchone()
        else:
            cur.execute("SELECT * FROM sponsors WHERE order_id=?", (order_id,))
            sponsor = cur.fetchone()
    pdf = invoice_pdf_buffer(order, job=job, sponsor=sponsor)
    return send_file(BytesIO(pdf), mimetype="application/pdf",
                     download_name=f"invoice_{order_id}.pdf")

if __name__ == "__main__":
    app.run(debug=True)
