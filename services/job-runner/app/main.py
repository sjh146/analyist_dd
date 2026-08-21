"""
job-runner — analyist_dd 온디맨드 전략 실행기 (내부망 전용).

- POST /run  {request_type, symbol?} → {request_id, status} — 백그라운드 잡 시작
- GET  /jobs/{job_id} → {status: queued|running|done|failed, result, error, ...}

잡은 asyncio 서브프로세스로 실행 (크래시 격리, 타임아웃 3600s).
결과 JSON은 잡 스크립트가 stdout 마지막 줄에 출력 → result에 저장.
동시에 리포트 파일(reports/*.json)도 기록 → 기존 파일 기반 엔드포인트와 호환.

인증: X-Internal-Api-Key (INTERNAL_API_KEY env, 미설정 시 fail-closed 503).
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
INTERNAL_CONFIGURED = bool(INTERNAL_API_KEY)

ALLOWED_TYPES = {"backtest", "swing_screener", "factor_report", "close_screener"}
JOB_SCRIPTS = {
    "backtest": ["python", "/app/app/scripts/run_backtest_job.py"],
    "swing_screener": ["python", "/app/app/scripts/run_swing_job.py"],
    "factor_report": ["python", "/app/app/scripts/run_factor_job.py"],
    "close_screener": ["python", "/app/app/scripts/run_close_job.py"],
}
RUN_CWD = "/opt/xgboost-ml"
JOB_TIMEOUT = 3600

app = FastAPI(title="analyist job-runner", version="1.0.0")

# 인메모리 잡 저장소 — 컨테이너 재시작 시 유실 (결제 연동 분석은 완료 폴링 후 소비하므로 허용)
JOBS: dict = {}


def _check_key(x_internal_api_key: str | None):
    if not INTERNAL_CONFIGURED:
        raise HTTPException(status_code=503, detail="internal API not configured")
    if not x_internal_api_key or x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="invalid internal key")


class RunRequest(BaseModel):
    request_type: str
    symbol: str | None = None


async def _run_job(job: dict):
    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        proc = await asyncio.create_subprocess_exec(
            *JOB_SCRIPTS[job["request_type"]],
            cwd=RUN_CWD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=JOB_TIMEOUT)
        if proc.returncode != 0:
            job["status"] = "failed"
            tail = (stderr.decode("utf-8", "replace") or stdout.decode("utf-8", "replace"))
            job["error"] = tail[-4000:]
        else:
            out = stdout.decode("utf-8", "replace").strip()
            try:
                # 스크립트가 stdout 마지막 줄에 결과 JSON을 출력
                job["result"] = json_loads_last(out)
            except Exception:
                job["result"] = {"raw_output": out[-8000:]}
            job["status"] = "done"
    except asyncio.TimeoutError:
        job["status"] = "failed"
        job["error"] = f"job timeout ({JOB_TIMEOUT}s)"
    except Exception as e:  # pragma: no cover
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


def json_loads_last(text: str):
    """stdout에서 마지막 JSON 라인 추출 (스크립트 print 출력은 앞에 있어도 됨)."""
    import json
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("no JSON line in stdout")


@app.post("/run")
async def run_job(req: RunRequest, x_internal_api_key: str | None = Header(None)):
    _check_key(x_internal_api_key)
    if req.request_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="unsupported request type")
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "request_type": req.request_type,
        "symbol": req.symbol,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    JOBS[job_id] = job
    asyncio.create_task(_run_job(job))
    return {"request_id": job_id, "status": "queued", "request_type": req.request_type}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, x_internal_api_key: str | None = Header(None)):
    _check_key(x_internal_api_key)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/health")
async def health():
    return {"status": "ok", "configured": INTERNAL_CONFIGURED}
