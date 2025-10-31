from .db import init_db, db
from datetime import datetime, timedelta

def seed():
    init_db()
    demo_jobs = [
        dict(title="Senior Python Engineer (Django)",
             company="Datacraft GmbH",
             location="Berlin, DE",
             email="jobs@datacraft.example",
             logo_url="",
             description="Baue interne Microservices mit Django & FastAPI."),
        dict(title="ML Engineer (NLP)",
             company="Lingua AI AG",
             location="Zürich, CH",
             email="careers@lingua.example",
             logo_url="",
             description="Produktive NLP‑Pipelines, LLM‑Feintuning, MLOps."),
        dict(title="Data Engineer",
             company="BayernAnalytics",
             location="München, DE",
             email="hr@bayerna.example",
             logo_url="",
             description="ETL mit PySpark, Airflow, Lakehouse‑Architektur."),
    ]
    with db() as conn:
        cur = conn.cursor()
        for j in demo_jobs:
            cur.execute("""INSERT INTO jobs (title, company, location, email, logo_url, description, grace_expires_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (j["title"], j["company"], j["location"], j["email"], j["logo_url"], j["description"],
                         # Leerzeichen als SEP für SQLite-Kompatibilität
                         (datetime.utcnow() + timedelta(hours=72)).isoformat(sep=" ", timespec="seconds")))
    print("Demo-Jobs eingefügt.")

if __name__ == "__main__":
    seed()
