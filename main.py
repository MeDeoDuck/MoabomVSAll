#!/usr/bin/env python3
"""
YouTube Tech Product Review Analysis Service - Modularized Version

Main entry point for the FastAPI application.
All functionality is modularized into scripts/ folder.

Usage:
    python main.py
    python main.py 8001  # custom port
"""
import os
import sys
import uvicorn
from pathlib import Path

# Force UTF-8 stdout so Korean text and emoji print correctly on Windows cp949 consoles
sys.stdout.reconfigure(encoding="utf-8")
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

# Import database initialization
from scripts.database.schema import init_db

# Import API route registrations
from scripts.api.products import register_product_routes
from scripts.api.videos import register_video_routes
from scripts.api.sync import register_sync_routes
from scripts.api.admin import register_admin_routes
from video_selection_agent.api.routes import register_selection_routes
from scripts.tracking import UsageTrackingMiddleware, GATagMiddleware


# ============================================================================
# FASTAPI APP INITIALIZATION
# ============================================================================

app = FastAPI(title="YouTube Product Analysis Service")


# Add middleware to set UTF-8 charset for HTML responses
class UTF8CharsetMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["content-type"] = "text/html; charset=utf-8"
        return response


app.add_middleware(UTF8CharsetMiddleware)
app.add_middleware(GATagMiddleware, measurement_id=os.getenv("GA_MEASUREMENT_ID"))
app.add_middleware(UsageTrackingMiddleware)


# Ensure templates directory exists
TEMPLATES_DIR = Path("templates")
TEMPLATES_DIR.mkdir(exist_ok=True)


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    print("[STARTUP] Initializing database...")
    init_db()
    print("[STARTUP] Database ready")

    # 자동 시드: tech_products 에 seeded=true row 가 0 건일 때만 1회 실행.
    # 멱등이라 매 부팅 호출해도 두 번째 부팅부터는 count 쿼리 1회만(<10ms).
    # 어떤 실패도 부팅을 막지 않는다 (검색 후보 시드는 보조 기능).
    try:
        from scripts.import_seed_products import auto_import_if_empty
        result = auto_import_if_empty()
        if result.get("skipped"):
            print(f"[STARTUP] Seed auto-import skipped — {result.get('reason')}")
        else:
            print(
                f"[STARTUP] Seed auto-import done — "
                f"inserted={result.get('inserted')} "
                f"skip_duplicate={result.get('skip_duplicate')} "
                f"error={result.get('error')}"
            )
    except Exception as e:
        print(f"[STARTUP][WARN] Seed auto-import failed (booting anyway): {e}")

    # 임베딩 시맨틱 인덱스 빌드 (v2 고도화) — content_hash 비교로 변경분만
    # 재임베딩. 매 부팅 호출해도 두 번째 부터는 hash 비교 1회만(<100ms).
    # 어떤 실패도 부팅을 막지 않는다 — semantic 단계는 DB+alias+Serper 의
    # 보조이므로 graceful degrade.
    try:
        import os as _os
        from scripts.api.suggest_vector import build_index_if_needed
        from scripts.config import SUGGEST_VECTOR_DB_PATH
        json_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            "seeds", "manual_products.json",
        )
        vec = build_index_if_needed(json_path, SUGGEST_VECTOR_DB_PATH)
        print(
            f"[STARTUP] Vector index ready — built={vec.get('built')} "
            f"cached={vec.get('cached')} embed_ms={vec.get('embed_ms')} "
            f"reason={vec.get('reason')!r}"
        )
    except Exception as e:
        print(f"[STARTUP][WARN] Vector index build failed (booting anyway): {e}")


# ============================================================================
# REGISTER ALL ROUTES
# ============================================================================

print("[STARTUP] Registering API routes...")
register_product_routes(app)
register_video_routes(app)
register_sync_routes(app)
register_admin_routes(app)
register_selection_routes(app)
print("[STARTUP] All routes registered")


# ============================================================================
# APP ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    
    # Allow command line override: python main.py 8001
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    
    print(f"\n{'='*70}")
    print(f"  YouTube Tech Product Review Analysis Service")
    print(f"  Modularized version - All code in scripts/ folder")
    print(f"{'='*70}")
    print(f"  🚀 Starting server on http://0.0.0.0:{port}")
    print(f"  📁 Project root: {Path.cwd()}")
    print(f"  📋 Templates: {TEMPLATES_DIR}")
    print(f"{'='*70}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
