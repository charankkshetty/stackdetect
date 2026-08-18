"""stackdetect — FastAPI entrypoint.

GET /       the scan page
GET /health liveness
POST /scan  scan one domain and return its verdict

Detection lives in app/orchestrator.py; credit safety lives in app/ledger.py.
This module stays thin: request in, verdict out.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import ledger, orchestrator

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="stackdetect", version="0.1.0")

# Open CORS so the static page can call the API from any origin.
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
async def scan(req: ScanRequest) -> dict:
    """Scan one domain.

    A detector failure never reaches here — the orchestrator isolates each one
    and reports its status in signals_summary. Only invalid input is rejected.
    """
    # Accept anything reasonable a demo user might paste — a full URL, a www
    # prefix, a deep subdomain — and reduce it to the registrable root before
    # validating. Normalising after validation would still reject URLs.
    domain = orchestrator.normalise_domain_input(req.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="domain is required")
    try:
        ledger.normalise_domain(domain)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"not a valid domain: {req.domain!r}")

    verdict = await orchestrator.scan(domain)
    # Real ledger counts, not placeholders. Nothing paid runs yet, so these
    # stay at zero until the enrichers are wired in.
    verdict["credits"] = ledger.usage()
    return verdict
