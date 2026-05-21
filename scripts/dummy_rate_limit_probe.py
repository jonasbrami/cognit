"""Throwaway script used to host a dummy PR for reproducing rate-limit behavior in
`quizz take`. Safe to delete — it's not imported by the package.

The functions below are intentionally simple and unrelated to anything else so the
quiz generator has a small, self-contained surface to ask questions about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bucket:
    """Token bucket with a fixed capacity and a constant refill rate."""

    capacity: int
    refill_per_second: float


class RateLimiter:
    """Single-bucket rate limiter.

    Not threadsafe by design — the probe runs in a single thread.
    """

    def __init__(self, bucket: Bucket) -> None:
        self._bucket = bucket
        self._tokens = float(bucket.capacity)
        self._last_refill_ts = 0.0

    def _refill(self, now_ts: float) -> None:
        elapsed = max(0.0, now_ts - self._last_refill_ts)
        self._tokens = min(
            float(self._bucket.capacity),
            self._tokens + elapsed * self._bucket.refill_per_second,
        )
        self._last_refill_ts = now_ts

    def try_acquire(self, now_ts: float, cost: int = 1) -> bool:
        """Attempt to take `cost` tokens; returns True on success, False if throttled."""
        self._refill(now_ts)
        if self._tokens < cost:
            return False
        self._tokens -= cost
        return True

    def available(self, now_ts: float) -> float:
        self._refill(now_ts)
        return self._tokens


def simulate(events_per_second: float, duration_s: float, bucket: Bucket) -> int:
    """Run a fixed-rate workload through the limiter and return the drop count."""
    limiter = RateLimiter(bucket)
    step = 1.0 / events_per_second
    dropped = 0
    t = 0.0
    while t < duration_s:
        if not limiter.try_acquire(t):
            dropped += 1
        t += step
    return dropped


def main() -> None:
    bucket = Bucket(capacity=10, refill_per_second=2.0)
    for rate in (1.0, 2.0, 5.0, 10.0):
        drops = simulate(rate, 30.0, bucket)
        print(f"rate={rate:>4.1f} req/s -> {drops:>3d} drops over 30s")


if __name__ == "__main__":
    main()
