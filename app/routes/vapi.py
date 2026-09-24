"""Vapi webhook adapter: translates Vapi's wire format into plain tool calls.

Vapi POSTs every server event to one URL. We handle two:
  - "tool-calls":         run each requested tool, reply {"results": [...]}
  - "end-of-call-report": store transcript + summary, linked to the patient
Everything else (status updates, etc.) is acknowledged and ignored.

These responses use Vapi's protocol shape, not the REST envelope, because
Vapi (not a human client) is the consumer.
"""
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import service
from app.agent_tools import CallContext, run_tool
from app.config import settings
from app.db import get_db
from app.errors import ApiError

log = logging.getLogger("app.vapi")
router = APIRouter(prefix="/vapi", tags=["vapi"])


def _verify_secret(request: Request) -> None:
    expected = settings.vapi_webhook_secret
    if not expected:
        log.warning("VAPI_WEBHOOK_SECRET is not set; webhook is unauthenticated")
        return
    provided = request.headers.get("x-vapi-secret", "")
    auth = request.headers.get("authorization", "")
    if not provided and auth.lower().startswith("bearer "):
        provided = auth[7:]
    if not provided or not hmac.compare_digest(provided, expected):
        raise ApiError(401, "unauthorized", "Invalid or missing webhook secret")


def _call_context(message: dict) -> CallContext:
    call = message.get("call") or {}
    customer = call.get("customer") or message.get("customer") or {}
    return CallContext(call_id=call.get("id"), caller_number=customer.get("number"))


def _tool_args(tool_call: dict) -> dict:
    fn = tool_call.get("function") or {}
    args = fn.get("arguments", tool_call.get("arguments")) or {}
    if isinstance(args, str):  # Vapi may send arguments as a JSON string
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return args if isinstance(args, dict) else {}


@router.post("/webhook")
def vapi_webhook(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    _verify_secret(request)
    message = payload.get("message") or {}
    message_type = message.get("type")
    ctx = _call_context(message)

    if message_type == "tool-calls":
        results = []
        for tool_call in message.get("toolCallList") or []:
            name = (tool_call.get("function") or {}).get("name") or tool_call.get("name")
            args = _tool_args(tool_call)
            result = run_tool(db, name, args, ctx)
            log.info("tool_call call_id=%s tool=%s status=%s", ctx.call_id, name, result.get("status"))
            results.append({"toolCallId": tool_call.get("id"), "result": json.dumps(result)})
        return {"results": results}

    if message_type == "end-of-call-report":
        artifact = message.get("artifact") or {}
        analysis = message.get("analysis") or {}
        transcript = artifact.get("transcript") or message.get("transcript")
        summary = analysis.get("summary") or message.get("summary")
        ended_reason = message.get("endedReason")
        duration = message.get("durationSeconds")
        log.info(
            "call_ended call_id=%s reason=%s duration=%s summary=%s",
            ctx.call_id, ended_reason, duration, (summary or "")[:500],
        )
        if ctx.call_id:
            try:
                service.save_call_report(
                    db, ctx.call_id, ctx.caller_number, transcript, summary, ended_reason,
                    float(duration) if isinstance(duration, (int, float)) else None,
                )
            except SQLAlchemyError:
                log.exception("save_call_report_failed call_id=%s", ctx.call_id)
        return {"ok": True}

    return {"ok": True}
