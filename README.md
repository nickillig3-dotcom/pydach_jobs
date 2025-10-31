# PyDACH Jobs v1.1 — Auto‑Marketing

**Neue Features:**

- `/robots.txt`, `/sitemap.xml`, `/feed.xml`
- **Landingpages automatisch**: `/c/<stadt>`, `/s/<skill>`
- **Weekly Digest**: `/weekly` → redirect zur aktuellen Woche, `/weekly/YYYY-WW`
- **OpenGraph/Meta** in Base‑Template; **JSON‑LD JobPosting** auf Detailseiten

**Start (VS Code / Windows PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate
pip install -r requirements.txt
python -m app.seeds
flask --app app.app run --debug
```
Konfiguration: `app/config.py` (IBAN/Name/Preis/Token).
