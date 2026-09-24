# Realtime Bidding System — second-price sealed-bid auctions

**What it does:** A real auction engine. Create auctions with a reserve price,
place bids over WebSocket (or HTTP) with real validation, then close and settle.
Settlement is **second-price**: the highest bidder wins and pays
`max(second-highest bid, reserve price)`. Individual bid amounts stay sealed
until settlement — only bid counts are broadcast live.

**Lifecycle:** `open` → `close` → `settle`. Auctions can also auto-close after
`duration_seconds`.

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --port 8000
```

## API

- `POST /auctions` `{"item","reserve_price","duration_seconds?"}`
- `WS /ws/auctions/{id}` — send `{"bidder":"ann","amount":120}`
- `POST /auctions/{id}/bid` — HTTP bid (same validation)
- `POST /auctions/{id}/close`, `POST /auctions/{id}/settle`, `GET /auctions/{id}`

```bash
AID=$(curl -s -X POST localhost:8000/auctions -H 'Content-Type: application/json' \
  -d '{"item":"GPU","reserve_price":100}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
curl -s -X POST localhost:8000/auctions/$AID/close
curl -s -X POST localhost:8000/auctions/$AID/settle
```

## Tests

```bash
pytest -q   # settlement math, validation, lifecycle, live WebSocket bidding
```
