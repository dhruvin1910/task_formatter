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


@app.get("/admin", response_class=HTMLResponse)
def admin():
    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, formatted_text, created_at FROM work_log ORDER BY created_at DESC")
        rows = cur.fetchall()
    conn.close()

    rows_html = ""
    for row in rows:
        dt = row["created_at"]
        date_str = dt.strftime("%a, %d %b %Y · %H:%M")
        text = row["formatted_text"].replace("\n", "<br>")
        rows_html += f"""
        <tr>
            <td>{row['id']}</td>
            <td>{date_str}</td>
            <td>{text}</td>
            <td><button onclick="deleteRow({row['id']})" class="del-btn">delete</button></td>
        </tr>"""

    total = len(rows)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Work Log — Admin</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg: #0f0f0f; --surface: #171717; --border: #2a2a2a;
    --text: #e8e8e8; --muted: #888; --dim: #555;
    --success: #4ade80; --error: #f87171;
    --font: 'JetBrains Mono', 'Fira Code', monospace;
  }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font); font-size: 13px; padding: 2rem 1.5rem; }}
  .app {{ max-width: 1000px; margin: 0 auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 1rem; border-bottom: 1px solid var(--border); margin-bottom: 1.5rem; }}
  .header h1 {{ font-size: 14px; font-weight: 500; }}
  .header a {{ font-size: 11px; color: var(--muted); text-decoration: none; }}
  .header a:hover {{ color: var(--text); }}
  .stats {{ font-size: 11px; color: var(--dim); margin-bottom: 1rem; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  td {{ padding: 12px; border-bottom: 1px solid var(--border); vertical-align: top; line-height: 1.7; }}
  td:first-child {{ color: var(--dim); width: 40px; }}
  td:nth-child(2) {{ color: var(--muted); white-space: nowrap; width: 180px; }}
  td:last-child {{ width: 80px; }}
  tr:hover td {{ background: var(--surface); }}
  .del-btn {{ background: transparent; border: 1px solid #3a2020; color: var(--error); padding: 3px 10px; border-radius: 4px; cursor: pointer; font-family: var(--font); font-size: 10px; }}
  .del-btn:hover {{ background: #2a1010; }}
  .empty {{ padding: 3rem; text-align: center; color: var(--dim); }}
  .actions {{ display: flex; gap: 8px; margin-bottom: 1.25rem; }}
  .btn {{ padding: 6px 16px; font-size: 11px; font-family: var(--font); border-radius: 6px; cursor: pointer; border: 1px solid var(--border); background: transparent; color: var(--muted); }}
  .btn:hover {{ background: var(--surface); color: var(--text); }}
  .status {{ font-size: 11px; color: var(--dim); margin-left: auto; align-self: center; }}
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <h1>work log — admin</h1>
    <a href="/">← back to app</a>
  </div>

  <div class="actions">
    <button class="btn" onclick="location.reload()">refresh</button>
    <button class="btn" onclick="clearAll()" style="border-color:#3a2020;color:var(--error)">clear all</button>
    <span class="status" id="status">{total} entr{'y' if total == 1 else 'ies'}</span>
  </div>

  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>date</th>
        <th>content</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="tbody">
      {'<tr><td colspan="4" class="empty">no entries yet</td></tr>' if not rows_html else rows_html}
    </tbody>
  </table>
</div>

<script>
async function deleteRow(id) {{
  if (!confirm('Delete this entry?')) return;
  const res = await fetch('/admin/delete/' + id, {{ method: 'DELETE' }});
  if (res.ok) location.reload();
}}

async function clearAll() {{
  if (!confirm('Delete ALL entries? This cannot be undone.')) return;
  const res = await fetch('/log', {{ method: 'DELETE' }});
  if (res.ok) location.reload();
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.delete("/admin/delete/{entry_id}")
def delete_entry(entry_id: int):
    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM work_log WHERE id = %s", (entry_id,))
    conn.close()
    return {"message": "deleted"}