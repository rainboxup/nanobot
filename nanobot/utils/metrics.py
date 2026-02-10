"""Lightweight in-process metrics for MVP observability.

This module intentionally avoids external dependencies (Prometheus client, etc.)
so small VPS deployments can still expose basic health counters/gauges.
"""

from __future__ import annotations

from collections import defaultdict
from threading import RLock

LabelTuple = tuple[tuple[str, str], ...]
MetricKey = tuple[str, LabelTuple]


class MetricsRegistry:
    """Thread-safe counter/gauge registry with optional labels."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[MetricKey, float] = defaultdict(float)
        self._gauges: dict[MetricKey, float] = {}

    def _label_tuple(self, labels: dict[str, str] | None = None, **kwargs: str) -> LabelTuple:
        merged: dict[str, str] = {}
        if labels:
            for k, v in labels.items():
                merged[str(k)] = str(v)
        for k, v in kwargs.items():
            merged[str(k)] = str(v)
        return tuple(sorted(merged.items()))

    def inc(
        self,
        name: str,
        amount: float = 1.0,
        *,
        labels: dict[str, str] | None = None,
        **kwargs: str,
    ) -> None:
        key = (str(name), self._label_tuple(labels, **kwargs))
        with self._lock:
            self._counters[key] = float(self._counters.get(key, 0.0) + float(amount))

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
        **kwargs: str,
    ) -> None:
        key = (str(name), self._label_tuple(labels, **kwargs))
        with self._lock:
            self._gauges[key] = float(value)

    def get_counter(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        **kwargs: str,
    ) -> float:
        key = (str(name), self._label_tuple(labels, **kwargs))
        with self._lock:
            return float(self._counters.get(key, 0.0))

    def get_gauge(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        **kwargs: str,
    ) -> float:
        key = (str(name), self._label_tuple(labels, **kwargs))
        with self._lock:
            return float(self._gauges.get(key, 0.0))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)

        out: dict[str, float] = {}
        for (name, labels), value in counters.items():
            out[self._render_key(name, labels)] = float(value)
        for (name, labels), value in gauges.items():
            out[self._render_key(name, labels)] = float(value)
        return out

    @staticmethod
    def _render_key(name: str, labels: LabelTuple) -> str:
        if not labels:
            return name
        body = ",".join(f"{k}={v}" for k, v in labels)
        return f"{name}{{{body}}}"


METRICS = MetricsRegistry()
