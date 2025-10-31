# PyDACH Jobs v1.2 — A/B‑Preis, Anti‑Spam, PDF‑Rechnung

**Neu in v1.2**  
- **A/B‑Preis** per Cookie: Gruppe A = 149 €, Gruppe B = 199 € (persistiert 90 Tage).  
- **Anti‑Spam**: Honeypot‑Feld + kleine Rechenfrage (ohne externe Dienste).  
- **Rechnung als PDF** (ReportLab), abrufbar im Admin neben jeder Bestellung.  
- Verbesserte Checkout‑Kopie (klarer Nutzen, Vertrauen).  
- `config.py` lädt automatisch `config_local.py`, wenn vorhanden.

## Update / Start
```powershell
python -m venv .venv
.\.venv\Scripts\Activate
pip install -r requirements.txt
python -m app.seeds
flask --app app.app run --debug
```
**Konfiguration:** `app/config_local.py` (lokal, nicht committen) oder Umgebungsvariablen.  
`app/config.py` ist nur ein Proxy/Default‑Loader.

## PDF‑Rechnung
Im Admin bei jeder Bestellung Button **„Rechnung“** → erzeugt eine PDF (Proforma, solange unbezahlt).  
Texte wie USt‑Hinweis kannst du in der Funktion `invoice_pdf()` anpassen.
