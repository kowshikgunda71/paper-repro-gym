#!/usr/bin/env python3
"""A stand-in 'paper artifact': a deterministic computation whose result we
claim in claims.json. No network, no randomness beyond a fixed seed. Writes
its metric to /output/metrics.json for the gym to score."""
import json
import os

# Leibniz partial sum for pi — deterministic, fixed iteration count.
def estimate_pi(n: int) -> float:
    s = 0.0
    for k in range(n):
        s += (-1.0) ** k / (2 * k + 1)
    return 4.0 * s

pi_hat = estimate_pi(1_000_000)
os.makedirs("/output", exist_ok=True)
with open("/output/metrics.json", "w") as fh:
    json.dump({"pi_estimate": round(pi_hat, 5)}, fh)
print(f"pi_estimate={pi_hat:.6f}")
