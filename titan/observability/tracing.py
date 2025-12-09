# titan/observability/tracing.py
from __future__ import annotations
import uuid
import time
import threading
from typing import Optional, List, Dict, Any

class Span:
    __slots__ = ("trace_id", "span_id", "parent_id", "name", "start", "end", "attributes")

    def __init__(self, trace_id: str, span_id: str, parent_id: Optional[str], name: str):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_id = parent_id
        self.name = name
        self.start = time.time()
        self.end: Optional[float] = None
        self.attributes: Dict[str, Any] = {}

    def finish(self) -> None:
        self.end = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "duration": (self.end - self.start) if self.end else None,
            "attributes": dict(self.attributes),
        }

class Tracer:
    """
    Minimal thread-safe tracer for local debug/tracing.
    """
    def __init__(self):
        self.local = threading.local()
        self._lock = threading.Lock()
        self._spans: Dict[str, List[Span]] = {}

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    def current_trace_id(self) -> Optional[str]:
        return getattr(self.local, "trace_id", None)

    def current_span_id(self) -> Optional[str]:
        return getattr(self.local, "span_id", None)

    def span(self, name: str):
        tracer = self

        class SpanCtx:
            def __enter__(self_inner):
                parent_trace = tracer.current_trace_id()
                parent_span = tracer.current_span_id()
                trace_id = parent_trace or tracer._new_id()
                span_id = tracer._new_id()[:12]
                self_inner.span = Span(trace_id, span_id, parent_span, name)
                tracer.local.trace_id = trace_id
                tracer.local.span_id = span_id
                return self_inner.span

            def __exit__(self_inner, exc_type, exc, tb):
                self_inner.span.finish()
                tracer._record_span(self_inner.span)
                # restore parent span id
                tracer.local.span_id = self_inner.span.parent_id

        return SpanCtx()

    def _record_span(self, span: Span) -> None:
        with self._lock:
            self._spans.setdefault(span.trace_id, []).append(span)

    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            spans = self._spans.get(trace_id, [])
            return [s.to_dict() for s in spans]

# singleton
tracer = Tracer()
