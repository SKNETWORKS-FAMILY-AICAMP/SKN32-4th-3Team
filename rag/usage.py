# path : rag/usage.py
"""
[RAG 파트] 토큰 사용량·비용 계측.

3차 보고서 8-3 "미측정 항목" 의 첫 줄이 이것입니다.

    비용 지표: usage_metadata 로깅 미구현

■ 무엇을 재는가

RAG 시스템의 API 비용은 두 군데서 발생합니다.

    색인 시   문서 임베딩        (1회성, 재색인마다 반복)
    질의 시   질문 임베딩        (질문 1건당 1회)
              LLM 답변 생성      (질문 1건당 1회, 입력+출력)

지금까지는 "임베딩 비용은 소액" 처럼 정성적으로만 적혀 있었습니다.
이 모듈은 실제 토큰 수를 API 응답에서 받아 적립합니다.
추정이 아니라 청구 근거와 같은 숫자입니다.

■ 왜 캐시가 중요한가

embeddings.py 는 이미 디스크 캐시를 씁니다. 캐시 적중은 API 호출이
없으므로 비용이 0 입니다. 이 모듈은 호출된 것만 세므로, 캐시 효과가
비용 절감으로 그대로 드러납니다. (cached_calls 로 적중 횟수도 셉니다)

■ 사용법

    from rag import usage

    with usage.collect() as meter:
        result = service.ask(question, region="seoul")

    print(meter.total_cost_usd)   # None 이면 가격표에 없는 모델
    print(meter.as_dict())

collect() 는 contextvars 를 쓰므로 요청·스레드마다 독립적입니다.
중첩해도 안쪽 것만 집계됩니다.

■ 가격표

USD / 1M 토큰. 2026-08 기준 공개 가격이며, **바뀔 수 있으므로**
settings.USAGE_PRICING 으로 덮어쓸 수 있게 했습니다.
가격표에 없는 모델은 토큰만 세고 비용은 None 으로 둡니다 —
0 으로 채우면 "비용 0원" 이라는 틀린 보고서가 조용히 나옵니다.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.conf import settings

# USD per 1,000,000 tokens
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # LLM
    "gpt-4o-mini":            {"input": 0.15, "output": 0.60},
    # 임베딩 (출력 토큰 없음)
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
}

_current: contextvars.ContextVar["Meter | None"] = contextvars.ContextVar(
    "rag_usage_meter", default=None
)


def pricing() -> dict[str, dict[str, float]]:
    table = dict(DEFAULT_PRICING)
    table.update(getattr(settings, "USAGE_PRICING", {}) or {})
    return table


@dataclass
class Call:
    kind: str            # "llm" | "embedding"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    priced: bool = True   # 가격표에 모델이 있었는가


@dataclass
class Meter:
    calls: list[Call] = field(default_factory=list)
    cached_calls: int = 0        # 캐시 적중 (API 호출 없음)
    unpriced_models: set = field(default_factory=set)

    # ---------------------------------------------------------- 집계

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def api_calls(self) -> int:
        return len(self.calls)

    @property
    def total_cost_usd(self) -> float | None:
        """가격표에 없는 모델이 하나라도 섞이면 None 을 돌려준다.

        부분 합계를 내면 실제보다 싸 보이는 숫자가 보고서에 실린다.
        """
        if self.unpriced_models:
            return None
        return round(sum(c.cost_usd or 0.0 for c in self.calls), 8)

    def cost_by_kind(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for kind in ("llm", "embedding"):
            subset = [c for c in self.calls if c.kind == kind]
            if not subset:
                out[kind] = 0.0
            elif any(not c.priced for c in subset):
                out[kind] = None
            else:
                out[kind] = round(sum(c.cost_usd or 0.0 for c in subset), 8)
        return out

    def as_dict(self) -> dict:
        return {
            "api_calls": self.api_calls,
            "cached_calls": self.cached_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.total_cost_usd,
            "cost_by_kind": self.cost_by_kind(),
            "unpriced_models": sorted(self.unpriced_models),
        }

    # ---------------------------------------------------------- 기록

    def add(self, kind: str, model: str, input_tokens: int, output_tokens: int = 0) -> Call:
        rates = pricing().get(model)
        if rates is None:
            self.unpriced_models.add(model)
            call = Call(kind, model, input_tokens, output_tokens, None, priced=False)
        else:
            cost = (
                input_tokens * rates.get("input", 0.0)
                + output_tokens * rates.get("output", 0.0)
            ) / 1_000_000
            call = Call(kind, model, input_tokens, output_tokens, cost, priced=True)
        self.calls.append(call)
        return call


# ─────────────────── 공개 API ───────────────────


@contextmanager
def collect():
    """이 블록 안에서 일어난 API 호출의 토큰·비용을 모은다."""
    meter = Meter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def record(kind: str, model: str, input_tokens: int, output_tokens: int = 0) -> None:
    """API 호출 직후에 부른다. 계측 중이 아니면 아무 일도 하지 않는다."""
    meter = _current.get()
    if meter is None:
        return
    meter.add(kind, model, int(input_tokens or 0), int(output_tokens or 0))


def record_cache_hit(count: int = 1) -> None:
    """캐시 적중(=API 호출 없음)을 센다."""
    meter = _current.get()
    if meter is None:
        return
    meter.cached_calls += int(count)


def active() -> bool:
    return _current.get() is not None


# ─────────────────── 응답 파서 ───────────────────
#
# SDK 마다 usage 필드 이름이 달라서 여기 모아 둔다.
# 응답 구조가 바뀌어도 호출부는 손대지 않는다.


def from_openai_chat(response) -> tuple[int, int]:
    """OpenAI chat.completions 응답에서 (input, output) 토큰을 뽑는다."""
    u = getattr(response, "usage", None)
    if u is None:
        return (0, 0)
    return (
        int(getattr(u, "prompt_tokens", 0) or 0),
        int(getattr(u, "completion_tokens", 0) or 0),
    )


def from_openai_embedding(response) -> int:
    """OpenAI embeddings 응답에서 입력 토큰 수를 뽑는다."""
    u = getattr(response, "usage", None)
    if u is None:
        return 0
    return int(getattr(u, "prompt_tokens", 0) or getattr(u, "total_tokens", 0) or 0)


def from_gemini(response) -> tuple[int, int]:
    """Gemini 응답의 usage_metadata 에서 (input, output) 토큰을 뽑는다."""
    u = getattr(response, "usage_metadata", None)
    if u is None:
        return (0, 0)
    return (
        int(getattr(u, "prompt_token_count", 0) or 0),
        int(getattr(u, "candidates_token_count", 0) or 0),
    )
