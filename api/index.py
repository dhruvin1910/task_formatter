from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx
import os
from datetime import datetime
from pathlib import Path
import pymysql
import pymysql.cursors

load_dotenv()

app = FastAPI(title="Daily Task Formatter")

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
MYSQL_HOST     = os.getenv("MYSQL_ADDON_HOST", "")
MYSQL_PORT     = int(os.getenv("MYSQL_ADDON_PORT", "3306"))
MYSQL_DB       = os.getenv("MYSQL_ADDON_DB", "")
MYSQL_USER     = os.getenv("MYSQL_ADDON_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_ADDON_PASSWORD", "")

HTML_CONTENT = open(Path(__file__).parent.parent / "static" / "index.html", encoding="utf-8").read()

_db_initialized = False


def get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def ensure_table():
    global _db_initialized
    if _db_initialized:
        return
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS work_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                formatted_text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.close()
    _db_initialized = True


class GenerateRequest(BaseModel):
    raw: str


class SaveRequest(BaseModel):
    formatted: str


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=HTML_CONTENT)


@app.post("/generate")
async def generate(req: GenerateRequest):
    if not req.raw.strip():
        raise HTTPException(400, "raw input is empty")

    prompt = f"""You are a work log formatter. Convert the raw notes below into a clean daily work summary.

Rules:
- Start with exactly: "Today's work"
- Add a blank line after "Today's work"
- List each task on its own line as a clear, professional one-line description
- Fix any spelling or grammar
- No bullet points, numbers, or dashes — plain task lines only
- Add a blank line between each task
- Output only the formatted result, nothing else

Raw notes:
{req.raw}"""

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            GROQ_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600,
                "temperature": 0.3,
            },
        )

    if res.status_code != 200:
        try:
            detail = res.json().get("error", {}).get("message", f"HTTP {res.status_code}")
        except Exception:
            detail = f"HTTP {res.status_code} — {res.text[:300]}"
        raise HTTPException(502, detail)

    try:
        result = res.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        raise HTTPException(502, f"Unexpected Groq response: {res.text[:300]}")

    return {"formatted": result}


@app.post("/save")
def save(req: SaveRequest):
    if not req.formatted.strip():
        raise HTTPException(400, "nothing to save")

    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO work_log (formatted_text) VALUES (%s)",
            (req.formatted.strip(),)
        )
    conn.close()
    return {"message": "saved"}


@app.get("/log")
def get_log():
    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT formatted_text, created_at FROM work_log ORDER BY created_at DESC")
        rows = cur.fetchall()
    conn.close()

    if not rows:
        return PlainTextResponse("no entries yet")

    separator = "─" * 40
    entries = []
    for row in rows:
        dt = row["created_at"]
        date_str = dt.strftime("%a, %d %b %Y")
        time_str = dt.strftime("%H:%M")
        entries.append(f"{separator}\n{date_str} · {time_str}\n{separator}\n{row['formatted_text']}")

    return PlainTextResponse("\n\n".join(entries))


@app.delete("/log")
def clear_log():
    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM work_log")
    conn.close()
    return {"message": "log cleared"}