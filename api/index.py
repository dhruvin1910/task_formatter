from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx
import os
from datetime import datetime, date
from pathlib import Path
import pymysql
import pymysql.cursors
from typing import Optional

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
                work_date DATE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # add work_date column if upgrading from old schema
        try:
            cur.execute("ALTER TABLE work_log ADD COLUMN work_date DATE")
            cur.execute("UPDATE work_log SET work_date = DATE(created_at) WHERE work_date IS NULL")
        except Exception:
            pass
    conn.close()
    _db_initialized = True


class GenerateRequest(BaseModel):
    raw: str


class SaveRequest(BaseModel):
    formatted: str
    work_date: Optional[str] = None  # YYYY-MM-DD, defaults to today


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

    work_date = req.work_date if req.work_date else date.today().isoformat()

    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO work_log (formatted_text, work_date) VALUES (%s, %s)",
            (req.formatted.strip(), work_date)
        )
    conn.close()
    return {"message": "saved"}


@app.get("/log")
def get_log():
    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT formatted_text, work_date FROM work_log ORDER BY work_date DESC")
        rows = cur.fetchall()
    conn.close()

    if not rows:
        return PlainTextResponse("no entries yet")

    separator = "─" * 40
    entries = []
    for row in rows:
        d = row["work_date"]
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        date_str = d.strftime("%a, %d %b %Y")
        entries.append(f"{separator}\n{date_str}\n{separator}\n{row['formatted_text']}")

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
        cur.execute("SELECT id, formatted_text, work_date FROM work_log ORDER BY work_date DESC")
        rows = cur.fetchall()
    conn.close()

    rows_html = ""
    for row in rows:
        d = row["work_date"]
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        date_str = d.strftime("%a, %d %b %Y")
        text = row["formatted_text"].replace("\n", "<br>")
        rows_html += f"""
        <tr>
            <td>{row['id']}</td>
            <td>{date_str}</td>
            <td>{text}</td>
            <td>
              <button onclick="deleteRow({row['id']})" class="del-btn">delete</button>
            </td>
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
    --success: #4ade80; --error: #f87171; --info: #60a5fa;
    --font: 'JetBrains Mono', 'Fira Code', monospace;
  }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font); font-size: 13px; padding: 2rem 1.5rem; }}
  .app {{ max-width: 1000px; margin: 0 auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 1rem; border-bottom: 1px solid var(--border); margin-bottom: 1.5rem; }}
  .header h1 {{ font-size: 14px; font-weight: 500; }}
  .header a {{ font-size: 11px; color: var(--muted); text-decoration: none; }}
  .header a:hover {{ color: var(--text); }}
  .add-form {{ border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 1.5rem; background: var(--surface); }}
  .add-form h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 10px; }}
  .form-row {{ display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; }}
  .form-row input[type=date] {{ padding: 6px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: var(--font); font-size: 12px; }}
  .form-row textarea {{ flex: 1; min-width: 200px; padding: 8px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: var(--font); font-size: 12px; line-height: 1.6; resize: vertical; min-height: 80px; }}
  .add-btn {{ padding: 6px 16px; font-size: 11px; font-family: var(--font); border-radius: 6px; cursor: pointer; background: var(--text); color: var(--bg); border: none; white-space: nowrap; }}
  .add-btn:hover {{ opacity: 0.85; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  td {{ padding: 12px; border-bottom: 1px solid var(--border); vertical-align: top; line-height: 1.7; }}
  td:first-child {{ color: var(--dim); width: 40px; }}
  td:nth-child(2) {{ color: var(--muted); white-space: nowrap; width: 160px; }}
  td:last-child {{ width: 80px; }}
  tr:hover td {{ background: var(--surface); }}
  .del-btn {{ background: transparent; border: 1px solid #3a2020; color: var(--error); padding: 3px 10px; border-radius: 4px; cursor: pointer; font-family: var(--font); font-size: 10px; }}
  .del-btn:hover {{ background: #2a1010; }}
  .empty {{ padding: 3rem; text-align: center; color: var(--dim); }}
  .actions {{ display: flex; gap: 8px; margin-bottom: 1.25rem; align-items: center; }}
  .btn {{ padding: 6px 16px; font-size: 11px; font-family: var(--font); border-radius: 6px; cursor: pointer; border: 1px solid var(--border); background: transparent; color: var(--muted); }}
  .btn:hover {{ background: var(--surface); color: var(--text); }}
  .status {{ font-size: 11px; color: var(--dim); margin-left: auto; }}
  .status.ok {{ color: var(--success); }}
  .status.err {{ color: var(--error); }}
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <h1>work log — admin</h1>
    <a href="/">← back to app</a>
  </div>

  <div class="add-form">
    <h2>add previous task</h2>
    <div class="form-row">
      <input type="date" id="prev-date" />
      <textarea id="prev-text" placeholder="Paste formatted task here...&#10;Today's work&#10;&#10;Task one&#10;&#10;Task two"></textarea>
      <button class="add-btn" onclick="addPrev()">add ↗</button>
    </div>
    <span class="status" id="add-status" style="margin-top:6px;display:block;"></span>
  </div>

  <div class="actions">
    <button class="btn" onclick="location.reload()">refresh</button>
    <button class="btn" onclick="clearAll()" style="border-color:#3a2020;color:var(--error)">clear all</button>
    <a href="/export/pdf" class="btn" style="text-decoration:none;border-color:#1a3a20;color:var(--success)">⬇ export pdf</a>
    <span class="status">{total} entr{'y' if total == 1 else 'ies'}</span>
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
// set default date to today
document.getElementById('prev-date').value = new Date().toISOString().slice(0,10);

async function addPrev() {{
  const d = document.getElementById('prev-date').value;
  const t = document.getElementById('prev-text').value.trim();
  const st = document.getElementById('add-status');
  if (!d || !t) {{ st.textContent = 'fill in date and content'; st.className = 'status err'; return; }}
  const res = await fetch('/save', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ formatted: t, work_date: d }})
  }});
  if (res.ok) {{
    st.textContent = 'saved!'; st.className = 'status ok';
    setTimeout(() => location.reload(), 800);
  }} else {{
    st.textContent = 'error saving'; st.className = 'status err';
  }}
}}

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


@app.get("/export/pdf")
def export_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from io import BytesIO
    from fastapi.responses import StreamingResponse

    ensure_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT formatted_text, work_date FROM work_log ORDER BY work_date DESC")
        rows = cur.fetchall()
    conn.close()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "title", parent=styles["Normal"],
        fontSize=18, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#111111"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "subtitle", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica",
        textColor=colors.HexColor("#888888"),
        spaceAfter=16,
    )
    date_style = ParagraphStyle(
        "date", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#222222"),
        spaceBefore=12, spaceAfter=6,
    )
    task_style = ParagraphStyle(
        "task", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica",
        textColor=colors.HexColor("#333333"),
        leading=18, spaceAfter=2,
    )

    story = []

    # Header
    story.append(Paragraph("Work Log", title_style))
    generated = datetime.now().strftime("%d %b %Y")
    story.append(Paragraph(f"Generated on {generated} · {len(rows)} entries", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dddddd"), spaceAfter=16))

    if not rows:
        story.append(Paragraph("No entries found.", task_style))
    else:
        for row in rows:
            d = row["work_date"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            date_str = d.strftime("%A, %d %B %Y")
            story.append(Paragraph(date_str, date_style))
            story.append(HRFlowable(width="100%", thickness=0.3, color=colors.HexColor("#eeeeee"), spaceAfter=6))

            lines = row["formatted_text"].strip().split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 4))
                elif line == "Today's work":
                    continue  # skip header line in PDF
                else:
                    story.append(Paragraph(f"• {line}", task_style))

            story.append(Spacer(1, 10))

    doc.build(story)
    buffer.seek(0)

    filename = f"work_log_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )