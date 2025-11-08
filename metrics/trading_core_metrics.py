from prometheus_client import start_http_server, Counter, Gauge, Histogram
import time, random

orders_total = Counter('trading_orders_total', 'Aantal geplaatste orders', ['pair','side'])
fills_total  = Counter('trading_fills_total', 'Aantal fills', ['pair'])
pnl_realized = Gauge('pnl_realized_eur_total', 'Gerealiseerde PnL in euro')
latency      = Histogram('order_latency_seconds', 'Order roundtrip latency (s)')

def simulate_metrics():
    pairs = ["BTC-EUR","ETH-EUR","GLMR-EUR"]
    while True:
        p = random.choice(pairs)
        orders_total.labels(p, random.choice(["buy","sell"])).inc()
        fills_total.labels(p).inc()
        pnl_realized.set(random.uniform(-5,15))
        latency.observe(random.uniform(0.1,1.2))
        time.sleep(5)

if __name__ == "__main__":
    start_http_server(9101)
    simulate_metrics()
