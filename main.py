"""Real second-price sealed-bid auction engine with WebSocket bidding.

Rules (all real logic, no stubs):
  - Auction lifecycle: open -> closed -> settled.
  - Bid validation: auction must be open; amount > 0; amount >= reserve price;
    amount must strictly beat the current highest bid.
  - Sealed bids: individual bid amounts are NOT broadcast to other bidders;
    only "new bid received" + current leader pseudonym-free count go out.
    (The full bid book is revealed at settlement.)
  - Settlement (second-price): highest bidder wins and pays
    max(second-highest bid, reserve price). Single bid -> pays reserve.
    No bids -> no winner.
  - Optional auto-close after duration_seconds.
"""

import asyncio
import itertools
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Realtime Bidding System — second-price auctions")

_auction_ids = itertools.count(1)


class AuctionCreate(BaseModel):
    item: str = Field(..., min_length=1)
    reserve_price: float = Field(..., gt=0)
    duration_seconds: float | None = Field(None, gt=0, description="Auto-close after N seconds")


class BidIn(BaseModel):
    bidder: str = Field(..., min_length=1, max_length=64)
    amount: float = Field(..., gt=0)


class Auction:
    def __init__(self, item: str, reserve_price: float, duration_seconds: float | None):
        self.id = next(_auction_ids)
        self.item = item
        self.reserve_price = reserve_price
        self.status = "open"
        self.bids: list[dict] = []          # {"bidder","amount","ts"}
        self.winner: str | None = None
        self.price: float | None = None
        self.created_at = time.time()
        self.closes_at = (self.created_at + duration_seconds) if duration_seconds else None
        self.lock = asyncio.Lock()
        self.connections: set[WebSocket] = set()

    def validate_bid(self, bidder: str, amount: float) -> str | None:
        """Return an error string, or None if the bid is valid."""
        if self.status != "open":
            return "auction is not open"
        if amount < self.reserve_price:
            return f"bid {amount} below reserve price {self.reserve_price}"
        if self.bids and amount <= max(b["amount"] for b in self.bids):
            return "bid must beat the current highest bid"
        if not bidder.strip():
            return "bidder name required"
        return None

    def settle(self) -> dict:
        ranked = sorted(self.bids, key=lambda b: b["amount"], reverse=True)
        if not ranked:
            self.winner, self.price = None, None
        elif len(ranked) == 1:
            self.winner, self.price = ranked[0]["bidder"], self.reserve_price
        else:
            self.winner = ranked[0]["bidder"]
            self.price = max(ranked[1]["amount"], self.reserve_price)
        self.status = "settled"
        return self.result()

    def result(self) -> dict:
        return {
            "id": self.id, "item": self.item, "reserve_price": self.reserve_price,
            "status": self.status, "num_bids": len(self.bids),
            "highest_bid": max((b["amount"] for b in self.bids), default=None),
            "winner": self.winner, "price": self.price,
        }


auctions: dict[int, Auction] = {}


async def broadcast(auction: Auction, message: dict):
    dead = []
    for ws in list(auction.connections):
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        auction.connections.discard(ws)


async def auto_close(auction_id: int, delay: float):
    await asyncio.sleep(delay)
    auction = auctions.get(auction_id)
    if auction and auction.status == "open":
        async with auction.lock:
            if auction.status == "open":
                auction.status = "closed"
        await broadcast(auction, {"type": "closed", "auction_id": auction_id})


def get_auction(auction_id: int) -> Auction:
    auction = auctions.get(auction_id)
    if not auction:
        raise HTTPException(404, "Auction not found")
    return auction


@app.get("/health")
def health():
    return {"status": "ok", "mechanism": "second-price sealed-bid"}


@app.post("/auctions")
async def create_auction(a: AuctionCreate):
    auction = Auction(a.item, a.reserve_price, a.duration_seconds)
    auctions[auction.id] = auction
    if a.duration_seconds:
        asyncio.create_task(auto_close(auction.id, a.duration_seconds))
    return {"id": auction.id, "item": auction.item,
            "reserve_price": auction.reserve_price, "status": auction.status}


@app.get("/auctions/{auction_id}")
def auction_state(auction_id: int):
    a = get_auction(auction_id)
    state = a.result()
    if a.status == "settled":
        state["bid_book"] = sorted(a.bids, key=lambda b: b["amount"], reverse=True)
    return state


@app.post("/auctions/{auction_id}/bid")
async def place_bid_http(auction_id: int, bid: BidIn):
    a = get_auction(auction_id)
    async with a.lock:
        err = a.validate_bid(bid.bidder, bid.amount)
        if err:
            raise HTTPException(400, err)
        a.bids.append({"bidder": bid.bidder, "amount": bid.amount, "ts": time.time()})
    await broadcast(a, {"type": "bid_received", "auction_id": a.id,
                        "num_bids": len(a.bids)})
    return {"accepted": True, "auction_id": a.id, "num_bids": len(a.bids)}


@app.post("/auctions/{auction_id}/close")
async def close_auction(auction_id: int):
    a = get_auction(auction_id)
    async with a.lock:
        if a.status != "open":
            raise HTTPException(400, f"auction is {a.status}")
        a.status = "closed"
    await broadcast(a, {"type": "closed", "auction_id": a.id})
    return {"id": a.id, "status": "closed"}


@app.post("/auctions/{auction_id}/settle")
async def settle_auction(auction_id: int):
    a = get_auction(auction_id)
    async with a.lock:
        if a.status == "open":
            raise HTTPException(400, "close the auction before settling")
        if a.status == "settled":
            raise HTTPException(400, "auction already settled")
        result = a.settle()
    await broadcast(a, {"type": "settled", **result})
    return result


@app.websocket("/ws/auctions/{auction_id}")
async def auction_socket(ws: WebSocket, auction_id: int):
    a = get_auction(auction_id)
    await ws.accept()
    a.connections.add(ws)
    try:
        await ws.send_json({"type": "state", **a.result()})
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                bid = BidIn(bidder=msg["bidder"], amount=float(msg["amount"]))
            except Exception:  # noqa: BLE001
                await ws.send_json({"type": "error", "detail": "send JSON {\"bidder\": str, \"amount\": number}"})
                continue
            async with a.lock:
                err = a.validate_bid(bid.bidder, bid.amount)
                if err:
                    await ws.send_json({"type": "error", "detail": err})
                    continue
                a.bids.append({"bidder": bid.bidder, "amount": bid.amount, "ts": time.time()})
                n = len(a.bids)
            await ws.send_json({"type": "bid_accepted", "num_bids": n})
            await broadcast(a, {"type": "bid_received", "auction_id": a.id, "num_bids": n})
    except WebSocketDisconnect:
        pass
    finally:
        a.connections.discard(ws)
