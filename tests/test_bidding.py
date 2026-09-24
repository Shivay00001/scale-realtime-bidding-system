"""Real tests: second-price settlement math, validation, lifecycle, WebSocket."""
from fastapi.testclient import TestClient
from main import app, Auction, auctions

client = TestClient(app)


def test_second_price_settlement_math():
    a = Auction("laptop", 50.0, None)
    a.bids = [
        {"bidder": "x", "amount": 100.0, "ts": 1},
        {"bidder": "y", "amount": 150.0, "ts": 2},
        {"bidder": "z", "amount": 120.0, "ts": 3},
    ]
    res = a.settle()
    assert res["winner"] == "y"      # highest bidder wins
    assert res["price"] == 120.0     # pays second-highest
    assert res["status"] == "settled"


def test_single_bid_pays_reserve():
    a = Auction("phone", 80.0, None)
    a.bids = [{"bidder": "x", "amount": 200.0, "ts": 1}]
    res = a.settle()
    assert res["winner"] == "x" and res["price"] == 80.0


def test_no_bids_no_winner():
    a = Auction("nothing", 10.0, None)
    res = a.settle()
    assert res["winner"] is None and res["price"] is None


def test_bid_validation_rules():
    a = Auction("tv", 100.0, None)
    assert a.validate_bid("ann", 50.0) is not None      # below reserve
    assert a.validate_bid("ann", 0) is not None         # non-positive
    assert a.validate_bid("ann", 120.0) is None         # ok
    a.bids.append({"bidder": "ann", "amount": 120.0, "ts": 1})
    assert a.validate_bid("bob", 120.0) is not None     # must strictly beat
    assert a.validate_bid("bob", 119.0) is not None
    assert a.validate_bid("bob", 121.0) is None
    a.status = "closed"
    assert a.validate_bid("bob", 500.0) is not None     # closed


def test_http_lifecycle_three_bidders():
    r = client.post("/auctions", json={"item": "GPU", "reserve_price": 100.0})
    aid = r.json()["id"]
    assert r.json()["status"] == "open"

    # below reserve rejected
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "a", "amount": 50}).status_code == 400
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "a", "amount": 100}).status_code == 200
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "b", "amount": 150}).status_code == 200
    # not beating highest rejected
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "c", "amount": 140}).status_code == 400
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "c", "amount": 120}).status_code == 400
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "c", "amount": 160}).status_code == 200

    # settle before close rejected
    assert client.post(f"/auctions/{aid}/settle").status_code == 400
    assert client.post(f"/auctions/{aid}/close").status_code == 200
    # bid after close rejected
    assert client.post(f"/auctions/{aid}/bid", json={"bidder": "d", "amount": 999}).status_code == 400

    res = client.post(f"/auctions/{aid}/settle").json()
    assert res["winner"] == "c" and res["price"] == 150.0
    state = client.get(f"/auctions/{aid}").json()
    assert state["status"] == "settled" and len(state["bid_book"]) == 3


def test_websocket_bidding():
    r = client.post("/auctions", json={"item": "WS-item", "reserve_price": 10.0})
    aid = r.json()["id"]
    with client.websocket_connect(f"/ws/auctions/{aid}") as ws:
        state = ws.receive_json()
        assert state["type"] == "state" and state["status"] == "open"
        ws.send_json({"bidder": "w1", "amount": 25})
        ack = ws.receive_json()
        assert ack["type"] == "bid_accepted"
        note = ws.receive_json()  # broadcast of own bid
        assert note["type"] == "bid_received" and note["num_bids"] == 1
        ws.send_json({"bidder": "w2", "amount": 5})  # below reserve
        err = ws.receive_json()
        assert err["type"] == "error"
    # bid landed via websocket
    assert client.get(f"/auctions/{aid}").json()["num_bids"] == 1
