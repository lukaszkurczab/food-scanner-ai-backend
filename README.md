# food-scanner-ai-backend

Minimalny, produkcyjny szkielet backendu FastAPI dla aplikacji CaloriAI / food-scanner-ai.

## Wymagania

- Python 3.11+

## Struktura

```text
app/
  main.py
  api/
  core/
  services/
  models/
  schemas/
  db/
tests/
requirements.txt
.gitignore
README.md
```

## Uruchomienie lokalne

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API healthcheck:

```text
GET http://127.0.0.1:8000/api/v1/health
```

## Testy

```bash
pytest -q
```
