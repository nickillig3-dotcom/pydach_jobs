from flask import Flask, render_template, request, redirect, url_for, send_file, abort, flash, Response
from datetime import datetime, timedelta, date
from io import BytesIO
import os
import random
import string
import re
import unicodedata

from .config import SITE_NAME, OWNER_NAME, IBAN, BIC, PRICE_EUR, FEATURE_DAYS, FEATURE_GRACE_HOURS, ADMIN_TOKEN
from .db import db, init_db
from .payment import make_epc_qr_png

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")

# Flask 3.1+: direkt initialisieren
init_db()

def now():
    return datetime.utcnow()

def order_reference(order_id: int) -> str:
    rand = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PYDACH-{order_id:05d}-{rand}"

@app.context_processor
def inject_globals():
    return dict(SITE_NAME=SITE_NAME, price_eur=PRICE_EUR)

# --- Housekeeping: Grace- und Featured-Flags automatisch pflegen ---
@app.before_request
def housekeeping():
    with db() as conn:
        cur = conn.cursor()
        # Grace abgelaufen?
        cur.execute("""
            UPDATE jobs
            SET grace_expires_at = NULL
            WHERE grace_expires_at IS NOT NULL
              AND datetime(grace_expires_at) <= datetime('now')
        """)
        # Featured nur mit Zahlung in letzten FEATURE_DAYS
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

# --- Helpers für Marketing ---
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
    "pytorch": ["pytorch", "torch "],
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
SKILL_LABEL = {
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
    "pandas": "Pandas", "numpy": "NumPy", "sklearn": "scikit-learn",
    "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "spark": "Apache Spark", "airflow": "Apache Airflow", "kafka": "Apache Kafka",
    "kubernetes": "Kubernetes", "docker": "Docker",
    "aws": "AWS", "azure": "Azure", "gcp": "Google Cloud (GCP)",
    "sql": "SQL", "etl": "ETL", "mlops": "MLOps", "nlp": "NLP"
}

def collect_jobs():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE status='published' ORDER BY created_at DESC")
        return cur.fetchall()

def location_variants(loc: str):
    if not loc:
        return []
    parts = re.split(r"[,\-/|–—]+", loc)
    return [slugify(p.strip()) for p in parts if p.strip()]

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

    # Top-Themen für Startseite
    # Städte
    city_counts = {}
    for j in jobs:
        for ls in location_variants(j.get("location","")):
            if ls:
                city_counts[ls] = city_counts.get(ls, 0) + 1
    # Namen wieder hübsch machen (erste Variante pro Slug)
    city_name = {}
    for j in jobs:
        loc = j.get("location") or ""
        for part in re.split(r"[,\-/|–—]+", loc):
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

    return render_template("index.html",
                           jobs=jobs,
                           top_cities=top_cities,
                           top_skills=top_skills,
                           meta_title=f"{SITE_NAME} — Python‑Jobs im DACH‑Raum",
                           meta_desc="Spezialisiertes Jobboard für Python in DE/AT/CH. Schnelles Posting, Zahlung per SEPA‑Überweisung (EPC‑QR).")

@app.route("/jobs/new", methods=["GET", "POST"])
def post_job():
    if request.method == "POST":
        title = request.form.get("title","").strip()
        company = request.form.get("company","").strip()
        location = request.form.get("location","").strip()
        email = request.form.get("email","").strip()
        logo_url = request.form.get("logo_url","").strip()
        description = request.form.get("description","").strip()
        if not title or not company or not description:
            flash("Titel, Unternehmen und Beschreibung sind Pflichtfelder.", "error")
            return render_template("post_job.html")
        # Grace
        grace_until = (now() + timedelta(hours=FEATURE_GRACE_HOURS)).isoformat(sep=" ", timespec="seconds")
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO jobs (title, company, location, email, logo_url, description, grace_expires_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (title, company, location, email, logo_url, description, grace_until))
            job_id = cur.lastrowid
            price_cents = int(round(PRICE_EUR * 100))
            cur.execute("""INSERT INTO orders (job_id, price_cents, currency, reference)
                           VALUES (?,?,?,?)""", (job_id, price_cents, "EUR", "TEMP"))
            order_id = cur.lastrowid
            ref = order_reference(order_id)
            cur.execute("UPDATE orders SET reference=? WHERE id=?", (ref, order_id))
        return redirect(url_for("checkout", order_id=order_id))
    return render_template("post_job.html", meta_title=f"Job einstellen — {SITE_NAME}")

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
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200")
        orders = cur.fetchall()
        order_map = {o["id"]: o for o in orders}
        if orders:
            ids = tuple(o["job_id"] for o in orders)
            cur.execute(f"SELECT * FROM jobs WHERE id IN ({','.join(['?']*len(ids))})", ids)
            jobs = {j["id"]: j for j in cur.fetchall()}
        else:
            jobs = {}
    for o in orders:
        o["job"] = jobs.get(o["job_id"])
        o["amount"] = o["price_cents"]/100.0
    return render_template("admin.html", orders=orders, token=token, meta_title=f"Admin — {SITE_NAME}")

@app.post("/admin/order/<int:order_id>/mark_paid")
def mark_paid(order_id: int):
    token = request.args.get("token","")
    if token != ADMIN_TOKEN:
        abort(403)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE orders SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
        cur.execute("SELECT job_id FROM orders WHERE id=?", (order_id,))
        job_id = cur.fetchone()["job_id"]
        cur.execute("UPDATE jobs SET is_featured=1 WHERE id=?", (job_id,))
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
    amount_idx = None
    for i,h in enumerate(headers):
        if "verwendungszweck" in h or "purpose" in h or "reference" in h or "ref" in h:
            purpose_idx = i
        if "amount" in h or "betrag" in h:
            amount_idx = i
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
                cur.execute("UPDATE jobs SET is_featured=1 WHERE id=?", (o["job_id"],))
                updated += 1
            imported += 1
    flash(f"Import fertig. Einträge geprüft: {imported}, neu bezahlt: {updated}.", "success")
    return redirect(url_for("admin", token=token))

# --- Auto-Marketing: robots, sitemap, feed, Landingpages, Weekly ---
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
    # Job-Detailseiten
    for j in jobs[:500]:
        urls.append(f"<url><loc>{url_for('job_detail', job_id=j['id'], _external=True)}</loc><changefreq>weekly</changefreq></url>")
    # Städte
    city_set = {}
    for j in jobs:
        for ls in location_variants(j.get("location","")):
            if ls:
                city_set[ls] = city_set.get(ls, 0) + 1
    for slug, _cnt in list(sorted(city_set.items(), key=lambda x: x[1], reverse=True))[:50]:
        urls.append(f"<url><loc>{url_for('city_page', city_slug=slug, _external=True)}</loc><changefreq>weekly</changefreq></url>")
    # Skills
    skill_counts = {}
    for j in jobs:
        txt = f"{j['title']} {j.get('description','')}"     # <-- richtig
        for s in job_skills(txt):
            skill_counts[s] = skill_counts.get(s, 0) + 1
    for slug, _cnt in list(sorted(skill_counts.items(), key=lambda x: x[1], reverse=True))[:50]:
        urls.append(f"<url><loc>{url_for('skill_page', skill_slug=slug, _external=True)}</loc><changefreq>weekly</changefreq></url>")
    # Weekly (aktuelle und letzte 7 Wochen)
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
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
                # Nimm die Original-Schreibweise
                for part in re.split(r"[,\-/|–—]+", j.get("location","")):
                    if slugify(part) == city_slug:
                        display_name = part.strip().title()
                        break
    sel.sort(key=lambda j: (not featured_or_grace(j), j["created_at"]))
    return render_template("landing_city.html",
                           jobs=sel,
                           city=display_name or city_slug.title(),
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
    return render_template("landing_skill.html",
                           jobs=sel,
                           skill_label=label,
                           meta_title=f"{label}-Jobs (DACH) | {SITE_NAME}",
                           meta_desc=f"Python‑Jobs mit {label} im DACH‑Raum.")

@app.get("/weekly")
def weekly_current():
    today = date.today()
    y,w,_ = today.isocalendar()
    return redirect(url_for('weekly_by_id', year=y, week=w))

@app.get("/weekly/<int:year>-<int:week>")
def weekly_by_id(year: int, week: int):
    # ISO Woche → Montag berechnen
    # Finde Montag der Woche
    # Algorithmus: erster Donnerstag der Woche
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

if __name__ == "__main__":
    app.run(debug=True)
