import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from api.me import router as me_router
from errors import install_error_handling
from database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception:
        logger.exception("Database initialisation failed; continuing so the API can still start")
    yield

app = FastAPI(
    title="PrepWise API",
    description="Backend for the PrepWise past-paper repository and mock paper generator",
    version="0.1.0",
    lifespan=lifespan
)

# Must come before CORS so that error responses still get CORS headers
install_error_handling(app)

# CORS (Allow Frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://prepwise-opal-three.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(me_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "PrepWise API is running"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
