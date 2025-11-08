import os, json, math, pathlib, signal
from typing import Dict, Any, List, Tuple
from collections import defaultdict
from redis import Redis

# -------- Config (ENV) --------
CFG = {
    "REDIS_URL": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
    "SIGNAL_STREAM": os.getenv("SIGNAL_STREAM", "signals:baseline"),
    "REWARD_STREAM": os.getenv("BANDIT_REWARD_STREAM", "rewards"),
    "STATE_KEY": os.getenv("BANDIT_STATE_KEY", "bandit:state"),
    "ENABLED": os.getenv("BANDIT_ENABLED", "false").lower() in ("1", "true", "yes"),
    "ALGO": os.getenv("BANDIT_ALGO", "linucb"),      # linucb | thompson
    "MIN_EVENTS": int(os.getenv("BANDIT_MIN_EVENTS", "2000")),
    "EPS": float(os.getenv("BANDIT_EXPLORATION", "0.1")),
    "LIN_ALPHA": float(os.getenv("BANDIT_LIN_ALPHA", "1.5")),
    "LOG": os.getenv("BANDIT_LOG", "/srv/trading/logs/bandit.log"),
}

r = Redis.from_url(CFG["REDIS_URL"], decode_responses=True)
log_path = pathlib.Path(CFG["LOG"])
log_path.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    print(msg, flush=True)
    try:
        with open(log_path, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass

# -------- Graceful stop --------
running = True
def _stop(*_):
    global running
    running = False
signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

# -------- Features --------
def parse_signal(fields: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
    try:
        reasons = json.loads(fields.get("reasons", "[]"))
        details = json.loads(fields.get("details", "{}"))
    except Exception:
        reasons, details = [], {}
    market = details.get("market") or fields.get("market") or "UNKNOWN"
    x = {
        "score": float(fields.get("score", 0.0)),
        "wick_ratio": float(details.get("wick_ratio", 0.0)),
        "vol_std": float(details.get("vol_std", 0.0)),
        "spread_bps": float(details.get("spread_bps", 1e9)),
        "vol_last": float(details.get("vol_last", 0.0)),
        "vol_mean": float(details.get("vol_mean", 0.0)),
        "r_wick": 1.0 if any("wick>=" in r for r in reasons) else 0.0,
        "r_volstd": 1.0 if any("vol_std>=" in r for r in reasons) else 0.0,
        "r_volspike": 1.0 if any("volume>=" in r for r in reasons) else 0.0,
        "r_spreadok": 1.0 if any("spread<=" in r for r in reasons) else 0.0,
    }
    x["vol_ratio"] = (x["vol_last"] / x["vol_mean"]) if x["vol_mean"] > 0 else 0.0
    return market, x

def vectorize(x: Dict[str, Any]) -> List[float]:
    return [
        1.0,                          # bias
        x.get("score", 0.0),
        x.get("wick_ratio", 0.0),
        x.get("vol_std", 0.0),
        x.get("spread_bps", 1e9),
        x.get("vol_ratio", 0.0),
        x.get("r_wick", 0.0),
        x.get("r_volstd", 0.0),
        x.get("r_volspike", 0.0),
        x.get("r_spreadok", 0.0),
    ]

# -------- LinUCB --------
class LinUCB:
    def __init__(self, d: int, alpha: float):
        self.d = d
        self.alpha = alpha
        self.A = {"ACT": self._I(d), "SKIP": self._I(d)}
        self.b = {"ACT": [0.0] * d, "SKIP": [0.0] * d}

    def _I(self, d):
        return [[1.0 if i == j else 0.0 for j in range(d)] for i in range(d)]

    def _solve(self, M: List[List[float]], y: List[float]) -> List[float]:
        d = len(y)
        M = [row[:] for row in M]
        y = y[:]
        # forward
        for i in range(d):
            piv = M[i][i] or 1e-6
            inv = 1.0 / piv
            for j in range(i, d):
                M[i][j] *= inv
            y[i] *= inv
            for k in range(i + 1, d):
                f = M[k][i]
                for j in range(i, d):
                    M[k][j] -= f * M[i][j]
                y[k] -= f * y[i]
        # back
        x = [0.0] * d
        for i in range(d - 1, -1, -1):
            s = sum(M[i][j] * x[j] for j in range(i + 1, d))
            x[i] = y[i] - s
        return x

    def theta(self, arm: str) -> List[float]:
        return self._solve(self.A[arm], self.b[arm])

    def ucb(self, arm: str, x: List[float]) -> float:
        th = self.theta(arm)
        mu = sum(th[i] * x[i] for i in range(self.d))
        # x^T A^-1 x via solve
        z = self._solve(self.A[arm], x[:])
        conf = math.sqrt(max(0.0, sum(x[i] * z[i] for i in range(self.d))))
        return mu + self.alpha * conf

    def update(self, arm: str, x: List[float], reward: float):
        for i in range(self.d):
            for j in range(self.d):
                self.A[arm][i][j] += x[i] * x[j]
            self.b[arm][i] += reward * x[i]

# -------- Thompson (optioneel) --------
class Thompson:
    def __init__(self):
        self.s = defaultdict(float)
        self.f = defaultdict(float)
    def score(self, arm: str) -> float:
        import random
        a, b = 1.0 + self.s[arm], 1.0 + self.f[arm]
        return random.betavariate(a, b)
    def update(self, arm: str, reward: float):
        if reward > 0:
            self.s[arm] += reward
        else:
            self.f[arm] += (-reward)

# -------- State --------
def load_state():
    raw = r.get(CFG["STATE_KEY"])
    return json.loads(raw) if raw else {}

def save_state(obj):
    r.set(CFG["STATE_KEY"], json.dumps(obj))

# -------- Main --------
def main():
    log(f"[bandit] starting (enabled={CFG['ENABLED']}, algo={CFG['ALGO']}, min_events={CFG['MIN_EVENTS']})")
    ids = {"signals": "$", "rewards": "$"}
    state = load_state() or {}
    ids.update({k: state.get(k, ids[k]) for k in ids})
    total_seen = int(state.get("total_seen", 0))

    algo_name = CFG["ALGO"].lower()
    if algo_name == "linucb":
        d = len(vectorize({}))
        model = LinUCB(d, CFG["LIN_ALPHA"])
    else:
        model = Thompson()

    explored = 0
    while running:
        res = r.xread(streams={CFG["SIGNAL_STREAM"]: ids["signals"]}, block=1000, count=200)
        if not res:
            continue
        for _, msgs in res:
            for msg_id, fields in msgs:
                ids["signals"] = msg_id
                market, feat = parse_signal(fields)
                x = vectorize(feat)
                total_seen += 1

                # Gate: pas leren als ENABLED en voldoende data
                if (not CFG["ENABLED"]) or (total_seen < CFG["MIN_EVENTS"]):
                    if total_seen % 500 == 0:
                        log(f"[bandit] buffered={total_seen} (enabled={CFG['ENABLED']}, min={CFG['MIN_EVENTS']})")
                    continue

                import random
                if isinstance(model, LinUCB):
                    s_act = model.ucb("ACT", x)
                    s_skip = model.ucb("SKIP", x)
                    if random.random() < CFG["EPS"]:
                        arm = "ACT" if random.random() < 0.5 else "SKIP"; explored += 1
                    else:
                        arm = "ACT" if s_act >= s_skip else "SKIP"
                else:
                    s_act = model.score("ACT"); s_skip = model.score("SKIP")
                    arm = "ACT" if s_act >= s_skip else "SKIP"

                r.xadd("signals:bandit", {
                    "market": market,
                    "arm": arm,
                    "x": json.dumps(feat),
                    "scores": json.dumps({"act": s_act, "skip": s_skip}),
                    "t": fields.get("t", "")
                }, maxlen=200000, approximate=True)

        if total_seen % 200 == 0:
            save_state({"last_signal_id": ids["signals"], "last_reward_id": ids["rewards"], "total_seen": total_seen})

    save_state({"last_signal_id": ids["signals"], "last_reward_id": ids["rewards"], "total_seen": total_seen})
    log(f"[bandit] stopped; total_seen={total_seen}, explored={explored}")

if __name__ == "__main__":
    main()
