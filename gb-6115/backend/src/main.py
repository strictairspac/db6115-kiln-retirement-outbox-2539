import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scripts.seed_demo import seed, setup_schema
from src.config.settings import SEED_DEMO
from src.domain.scenario import CONFIG
from src.middlewares.errors import catch_errors
from src.middlewares.request_id import request_id
from src.routes import cases, retirement, runs, session, system


@asynccontextmanager
async def lifespan(_app):
    setup_schema()
    if SEED_DEMO:
        try:
            seed()
        except Exception:
            logging.getLogger(__name__).exception("Demo seed failed")
    yield


app = FastAPI(title=CONFIG["title"], lifespan=lifespan)
app.middleware("http")(catch_errors)
app.middleware("http")(request_id)
for group in (system, session, cases, runs):
    app.include_router(group.router)

# 炉体退役批次 · 放行判定：主路径 /batches、/ovens、/lines、/board，
# 另挂常见资源前缀别名，方便不同验收入口访问同一套实现。
app.include_router(retirement.router)
for prefix in ("/retirement", "/oven-retirement", "/kiln-retirement", "/release"):
    app.include_router(retirement.router, prefix=prefix)
