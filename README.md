# Daily Task Formatter

FastAPI app to convert raw task notes into a clean formatted work log.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Azure credentials
cp .env.example .env
# Edit .env and fill in AZURE_ENDPOINT and AZURE_API_KEY

# 3. Run
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000

## Project Structure

```
task_formatter/
├── main.py            # FastAPI backend
├── requirements.txt
├── .env.example
├── static/
│   └── index.html     # Frontend UI
└── logs/
    └── work_log.txt   # Auto-created, all tasks saved here
```

## API Endpoints

| Method | Path        | Description                    |
|--------|-------------|--------------------------------|
| GET    | /           | Serve the UI                   |
| POST   | /generate   | Format raw tasks via Azure LLM |
| POST   | /save       | Append formatted output to log |
| GET    | /log        | Read the full work log         |
| DELETE | /log        | Clear the work log             |

## Environment Variables

| Variable        | Description                          |
|-----------------|--------------------------------------|
| AZURE_ENDPOINT  | Full Azure OpenAI deployment URL     |
| AZURE_API_KEY   | Your Azure OpenAI API key            |
