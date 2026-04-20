"""WhatsApp webhook server for the medical appointment chatbot.

Receives incoming messages from the WhatsApp Business Cloud API,
deduplicates them via DynamoDB, forwards them to the agent service,
and sends the reply back to the patient.

Deployed as an AWS Lambda function via Mangum.
"""

import os
import time
import logging
from typing import Any

import requests
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from mangum import Mangum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_verify_token() -> str:
    """Read the WhatsApp webhook verification token from environment."""
    for key in ("WHATSAPP_VERIFY_TOKEN", "VERIFY_TOKEN"):
        value = os.getenv(key)
        if value:
            return value
    return ""


WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("WHATSAPP_PHONE_ID") or ""
AGENT_URL_BASE = os.getenv("AGENT_URL") or os.getenv("AGENT_SERVICE_URL") or ""
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
APPOINTMENTS_TABLE = os.getenv("APPOINTMENTS_TABLE", "appointments_sessions")
DEDUPE_TTL = int(os.getenv("DEDUPE_TTL", "600"))

app = FastAPI()

dynamo = boto3.resource("dynamodb")
tbl = dynamo.Table(APPOINTMENTS_TABLE)


def _dedupe_accept(message_id: str) -> bool:
    """Return True if this message ID hasn't been seen before (within TTL).

    Uses a DynamoDB conditional put to atomically check-and-insert.
    Prevents double-processing of webhook retries from Meta.
    """
    now = int(time.time())
    ttl = now + DEDUPE_TTL
    try:
        tbl.put_item(
            Item={"pk": f"dedupe:{message_id}", "sk": "meta", "created_at": now, "expiresAt": ttl},
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse incoming WhatsApp webhook payload and extract text messages.

    Handles text, button, and interactive (list/button reply) message types.
    """
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", []) or []
            contacts = value.get("contacts", []) or []
            wa_id = contacts[0].get("wa_id") if contacts else None
            for m in messages:
                msg_from = m.get("from") or wa_id
                msg_id = m.get("id")
                ts = m.get("timestamp")
                mtype = m.get("type")
                text = None
                if mtype == "text":
                    text = (m.get("text") or {}).get("body")
                elif mtype == "button":
                    text = (m.get("button") or {}).get("text")
                elif mtype == "interactive":
                    it = m.get("interactive") or {}
                    if "button_reply" in it:
                        text = (it.get("button_reply") or {}).get("title")
                    elif "list_reply" in it:
                        text = (it.get("list_reply") or {}).get("title")
                if msg_from and msg_id and text:
                    out.append({
                        "from": msg_from,
                        "id": msg_id,
                        "timestamp": ts,
                        "type": mtype,
                        "text": text,
                    })
    return out


def _send_whatsapp_text(to: str, body: str) -> dict[str, Any]:
    """Send a text message to a WhatsApp user via the Cloud API."""
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body[:4096]},
    }
    r = requests.post(url, json=data, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _call_agent_service(payload: dict[str, Any]) -> dict[str, Any]:
    """Forward a message to the agent service and return its response."""
    if not AGENT_URL_BASE:
        return {}
    url = AGENT_URL_BASE.rstrip("/") + "/message"
    r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Routes ──────────────────────────────────────────────────────

@app.get("/")
async def root():
    return JSONResponse({"ok": True, "endpoints": ["/health", "/webhook"]})


@app.get("/health")
async def health():
    return JSONResponse({"ok": True})


@app.get("/webhook")
async def verify(request: Request):
    """Handle WhatsApp webhook verification (hub.verify_token challenge)."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    env_token = _get_verify_token()
    if mode == "subscribe" and token == env_token:
        return PlainTextResponse(challenge or "")
    return JSONResponse(status_code=403, content={"error": "forbidden"})


@app.post("/webhook")
async def receive(request: Request):
    """Receive incoming WhatsApp messages, deduplicate, process, and reply."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    msgs = _extract_messages(payload)
    if not msgs:
        return JSONResponse({"ok": True})

    results: list[dict[str, Any]] = []
    for m in msgs:
        if not _dedupe_accept(m["id"]):
            continue
        try:
            agent_payload = {
                "session_id": f"wa:{m['from']}",
                "text": m["text"],
                "metadata": {
                    "channel": "whatsapp",
                    "from": m["from"],
                    "message_id": m["id"],
                    "timestamp": m["timestamp"],
                },
            }
            agent_resp = _call_agent_service(agent_payload)

            replies: list[str] = []
            if isinstance(agent_resp, dict):
                if isinstance(agent_resp.get("reply"), str):
                    replies = [agent_resp["reply"]]
                elif isinstance(agent_resp.get("replies"), list):
                    replies = [str(x) for x in agent_resp["replies"] if x is not None]
            if not replies:
                replies = ["Thank you, we're processing your request."]

            for body in replies:
                try:
                    results.append(_send_whatsapp_text(m["from"], body))
                except Exception as e:
                    logger.exception("send_whatsapp_text_failed")
                    results.append({"error": str(e)})
        except requests.HTTPError as e:
            logger.exception("agent_or_graph_http_error")
            results.append({"error": str(e)})
        except Exception as e:
            logger.exception("processing_error")
            results.append({"error": str(e)})

    return JSONResponse({"ok": True, "results": results})


handler = Mangum(app)
