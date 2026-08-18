"""stackdetect — FastAPI entrypoint.

Deploy-first skeleton: /scan returns a HARDCODED stub verdict so the whole
loop (browser -> API -> table) can be tested on Railway before any detector
exists. Real detection lands in app/orchestrator.py in a later step.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="stackdetect", version="0.1.0")

# Open CORS so the static page can call the API from any origin (including a
# file:// page or a different Railway domain).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    domain: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/scan")
def scan(req: ScanRequest) -> dict:
    """STUB. Returns a fixed verdict shaped like the real one.

    Replace the body with app.orchestrator.scan(req.domain) once the free
    detectors exist.
    """
    return {
        "domain": req.domain,
        "tools": [
            {
                "tool": "Snowflake",
                "signal": "ct_log",
                "confidence": 0.95,
                "evidence": "acme.snowflakecomputing.com",
            }
        ],
        "self_hosted_orchestrator": False,
        "credits": {
            "apollo_used": 0,
            "apollo_cap": 300,
            "parallel_used": 0,
            "parallel_cap": 300,
        },
    }
