"""FastAPI application: wires routers, error handlers and logging together.

Layers (each only talks to the one below it):
  routes/vapi.py      telephony adapter (Vapi wire format)
  agent_tools.py      voice tool logic
  routes/patients.py  REST API
  service.py          business rules + database access
  models.py           schema and constraints
"""
import logging
import sys

from fastapi import FastAPI

from app.errors import register_exception_handlers
from app.routes import pages, patients, vapi
from app.vercel_paths import VercelPathMiddleware

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="Patient Registration Voice Agent API",
    version="1.0.0",
    description="REST API and Vapi webhook for a voice-based patient intake agent.",
)
register_exception_handlers(app)
app.include_router(patients.router)
app.include_router(vapi.router)
app.add_middleware(VercelPathMiddleware)
app.include_router(pages.router)
