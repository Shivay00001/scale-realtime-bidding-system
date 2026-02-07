package main

import (
    "fmt"
    "math/rand"
    "net/http"
    "time"
)

type BidRequest struct {
    ID string
    FloorPrice float64
}

type BidResponse struct {
    BidderID string
    Price float64
}

func handleBid(w http.ResponseWriter, r *http.Request) {
    // Simulate latency budget of 100ms
    start := time.Now()
    
    // Auction logic
    bid := rand.Float64() * 10
    
    elapsed := time.Since(start)
    fmt.Printf("Bid processed in %s. Price: %.2f\n", elapsed, bid)
    
    w.WriteHeader(http.StatusOK)
    fmt.Fprintf(w, "Bid: %.2f", bid)
}

func main() {
    http.HandleFunc("/bid", handleBid)
    fmt.Println("RTB Engine running on :8000")
    http.ListenAndServe(":8000", nil)
}
