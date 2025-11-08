from prometheus_client import start_http_server, Counter, Gauge
import random, time

messages_ingested = Counter('bitvavo_messages_ingested_total','Aantal ontvangen websocket events',['market'])
latency_ws = Gauge('bitvavo_ws_latency_ms','Gemeten latency van Bitvavo WS in ms')

def simulate():
    mkts = ["BTC-EUR","ETH-EUR","GLMR-EUR"]
    while True:
        m = random.choice(mkts)
        messages_ingested.labels(m).inc(random.randint(1,5))
        latency_ws.set(random.uniform(5,30))
        time.sleep(2)

if __name__ == "__main__":
    start_http_server(9102)
    simulate()
