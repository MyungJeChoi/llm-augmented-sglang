from fastapi import FastAPI

from app.middlewares.request_id import RequestIdMiddleware
from app.routers import health, chat, kg, admin, metrics, agent


app = FastAPI(title="KG-backed NL2SQL Platform", version="0.3.0")

# Middleware
app.add_middleware(RequestIdMiddleware)

# Routers
app.include_router(health.router)
app.include_router(metrics.router, tags=["ops"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(agent.router, prefix="/agent", tags=["agent"])
app.include_router(kg.router, prefix="/kg", tags=["kg"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
