# Path: FLOW/titan/observability/tracing.py
from __future__ import annotations
import uuid
import time
import threading
from typing import Optional, List, Dict, Any


class Span:
    """
    A simple span object for distributed tracing.
    """
    __slots__ = ("trace_id", "span_id", "parent_id", "name", "start", "end", "attributes")

    def __init__(self, trace_id: str, span_id: str, parent_id: Optional[str], name: str):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_id = parent_id
        self.name = name
        self.start = time.time()
        self.end: Optional[float] = None
        self.attributes: Dict[str, Any] = {}

    def finish(self):
        self.end = time.time()

    def to_dict(self):
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "duration": (self.end - self.start) if self.end else None,
            "attributes": self.attributes,
        }


class Tracer:
    """
    A simple, thread-safe tracing engine.
    """
    def __init__(self):
        self.local = threading.local()
        self._lock = threading.Lock()
        self._spans: Dict[str, List[Span]] = {}  # trace_id -> list of spans

    def _new_trace_id(self) -> str:
        return uuid.uuid4().hex

    def _new_span_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def current_trace_id(self) -> Optional[str]:
        return getattr(self.local, "trace_id", None)

    def current_span_id(self) -> Optional[str]:
        return getattr(self.local, "span_id", None)

    def span(self, name: str):
        """
        Usage:
            with tracer.span("executor.run"):
                executor.execute(...)
        """

        tracer = self

        class SpanCtx:
            def __enter__(self):
                parent_trace = tracer.current_trace_id()
                parent_span = tracer.current_span_id()

                trace_id = parent_trace or tracer._new_trace_id()
                span_id = tracer._new_span_id()

                self.span = Span(trace_id, span_id, parent_span, name)
                tracer.local.trace_id = trace_id
                tracer.local.span_id = span_id

            def __exit__(self, exc_type, exc, tb):
                self.span.finish()
                tracer._record_span(self.span)

                # restore parent span_id
                tracer.local.span_id = self.span.parent_id

        return SpanCtx()

    def _record_span(self, span: Span):
        with self._lock:
            self._spans.setdefault(span.trace_id, []).append(span)

    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            spans = self._spans.get(trace_id, [])
            return [s.to_dict() for s in spans]


# Global tracer
tracer = Tracer()
