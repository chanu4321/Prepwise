from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import router as api_router

app = FastAPI(
    title="PrepWise API",
    description="Backend for the PrepWise past-paper repository and mock paper generator",
    version="0.1.0"
)

# CORS (Allow Frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://prepwise-opal-three.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "PrepWise API is running"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
