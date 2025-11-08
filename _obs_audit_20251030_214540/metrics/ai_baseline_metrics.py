from prometheus_client import start_http_server, Counter, Gauge
import random, time

signals_emitted = Counter('ai_signals_total','Aantal AI-signalen',['type'])
signal_score    = Gauge('ai_signal_score','Gemiddelde score laatste minuut')

def simulate():
    while True:
        signals_emitted.labels(random.choice(["buy","sell","hold"])).inc()
        signal_score.set(random.uniform(0,1))
        time.sleep(3)

if __name__ == "__main__":
    start_http_server(9103)
    simulate()
