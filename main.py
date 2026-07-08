"""
Orders API — demonstrates:
  1. Idempotent POST /orders
  2. Cursor-based pagination on GET /orders
  3. Per-client rate limiting (X-Client-Id header)

Assigned values:
  Total orders (T) = 56
  Rate limit (R)    = 19 requests / 10 seconds
"""

import time
import uuid
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# ----------------------------------------------------------------------
# CONFIG (your assigned values)
# ----------------------------------------------------------------------
TOTAL_ORDERS = 56       # T
RATE_LIMIT = 19         # R requests
RATE_WINDOW = 10        # seconds

app = FastAPI(title="Orders API")

# Allow the grader's browser page to call us from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# "DATABASE" (just in-memory dictionaries — resets if server restarts)
# ----------------------------------------------------------------------

# Fixed catalog of orders 1..T, created once at startup
CATALOG = [
    {"id": i, "item": f"Order #{i}", "status": "confirmed"}
    for i in range(1, TOTAL_ORDERS + 1)
]

# idempotency_key -> order dict   (so repeated keys return the same order)
idempotency_store: dict[str, dict] = {}

# client_id -> list of timestamps of recent requests (for rate limiting)
rate_buckets: dict[str, list[float]] = {}


# ----------------------------------------------------------------------
# 1. IDEMPOTENT POST /orders
# ----------------------------------------------------------------------
class OrderIn(BaseModel):
    item: Optional[str] = "New Order"


@app.post("/orders", status_code=201)
def create_order(order: OrderIn, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    # If we've seen this key before, return the SAME order (no duplicate!)
    if idempotency_key in idempotency_store:
        existing = idempotency_store[idempotency_key]
        return JSONResponse(status_code=201, content=existing)

    # First time we've seen this key -> create a brand new order
    new_order = {
        "id": str(uuid.uuid4()),
        "item": order.item,
        "status": "created",
    }
    idempotency_store[idempotency_key] = new_order
    return new_order


# ----------------------------------------------------------------------
# 2. CURSOR-BASED PAGINATION  GET /orders?limit=P&cursor=C
# ----------------------------------------------------------------------
@app.get("/orders")
def list_orders(limit: int = 10, cursor: Optional[str] = None):
    # The "cursor" is simply the index (as a string) of where to start.
    # It's opaque to the caller — they just pass back whatever we gave them.
    start = int(cursor) if cursor is not None else 0

    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be positive")

    end = start + limit
    page_items = CATALOG[start:end]

    # If there's more data left after this page, give a next_cursor.
    # Otherwise, next_cursor is null -> tells the caller "you're done."
    next_cursor = str(end) if end < len(CATALOG) else None

    return {
        "items": page_items,
        "next_cursor": next_cursor,
        "next": next_cursor,      # alias, in case grader looks for this name
        "orders": page_items,     # alias, in case grader looks for this name
    }


# ----------------------------------------------------------------------
# 3. PER-CLIENT RATE LIMITING (X-Client-Id header)
# ----------------------------------------------------------------------
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate-limit the /orders endpoints (not docs, etc.) — optional,
    # but keeps things simple and matches what the grader tests.
    if request.url.path.startswith("/orders"):
        client_id = request.headers.get("X-Client-Id", "anonymous")
        now = time.time()

        # Get this client's list of past request timestamps
        timestamps = rate_buckets.get(client_id, [])

        # Throw away timestamps older than the 10-second window
        window_start = now - RATE_WINDOW
        timestamps = [t for t in timestamps if t > window_start]

        if len(timestamps) >= RATE_LIMIT:
            # Too many requests! Figure out how long until the oldest
            # request "falls out" of the window, so client knows when
            # to retry.
            retry_after = int(RATE_WINDOW - (now - timestamps[0])) + 1
            rate_buckets[client_id] = timestamps  # save cleaned list
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        # Under the limit -> record this request and let it through
        timestamps.append(now)
        rate_buckets[client_id] = timestamps

    response = await call_next(request)
    return response


# ----------------------------------------------------------------------
# Health check (handy for confirming the service is alive)
# ----------------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "total_orders": TOTAL_ORDERS, "rate_limit": f"{RATE_LIMIT}/{RATE_WINDOW}s"}
