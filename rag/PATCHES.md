# 적용 패치 — BM25 하이브리드 검색 + 비용 지표

새 파일 4개를 먼저 넣고, 기존 파일 4개를 아래대로 고칩니다.
`찾기` 문자열은 현재 저장소에 있는 그대로입니다.

새 파일

    rag/tokenizer.py    rag/bm25_store.py    rag/scoring.py    rag/usage.py
    evals/run_search_eval.py

설치

    pip install rank_bm25 kiwipiepy

`kiwipiepy` 는 없어도 동작합니다(조사 제거 폴백). 다만 폴백은
오절단이 있어 품질이 떨어지므로 설치를 권합니다.

---

# A. `rag/service.py` — BM25·하이브리드 검색

## A-1. import (41행)

**찾기**

```python
from . import chunking, embeddings, vector_store
```

**바꾸기**

```python
from . import bm25_store, chunking, embeddings, scoring, vector_store
```

## A-2. `_effective_min_score()` 위임 (269~286행)

`def _effective_min_score(` 부터 `return settings.RAG_MIN_SCORE` 까지
함수 전체를 지우고 아래로 교체합니다. 판정 로직이 파이프라인별로
갈라져 `scoring.py` 로 옮겨갔습니다. 호출부 시그니처는 그대로입니다.

```python
def _effective_min_score(min_score: float | None) -> float:
    """유사도 임계값을 결정한다.

    파이프라인마다 score 스케일이 다르다는 문제가 생겨
    판정 로직은 rag/scoring.py 로 옮겼다.
    (RRF 는 순위 기반이라 이 임계값으로 판정하면 안 된다 — scoring.py 참고)
    """
    return scoring.effective_min_score(min_score)
```

## A-3. `_retrieve()` 에 bm25·hybrid 분기 (317~344행)

기존 함수 전체를 아래로 교체합니다. legacy·langchain 동작은
그대로 두고 두 갈래만 추가했습니다.

```python
def _retrieve(query: str, fetch_k: int, region: str | None = None) -> list[dict]:
    """질문에 대한 후보 청크를 가져온다. 여기가 유일한 분기점이다.

    반환 형식은 네 경로가 동일하다.
        [{"content", "title", "document_id", "owner_id",
          "source_type", "region", "score"}, ...]

    region 은 bm25/hybrid 에서만 쓴다. 후보 단계에서 다른 지역
    문서를 빼두면 BM25 점수 경쟁이 줄어 지역 문서가 살아남는다.
    (legacy/langchain 은 search() 의 사후 필터에 그대로 의존한다)
    """
    pipeline = settings.RAG_PIPELINE.lower()

    if pipeline == "langchain":
        from langchain_community.vectorstores import FAISS

        save_path = str(settings.INDEX_DIR / _LANGCHAIN_INDEX_DIR)
        embedding_fn = embeddings._get_langchain_embeddings_class()()
        store = FAISS.load_local(
            save_path,
            embedding_fn,
            allow_dangerous_deserialization=True,
        )
        return vector_store.search_with_langchain(store, query, fetch_k)

    if pipeline == "bm25":
        return bm25_store.search(query, fetch_k, region=region)

    if pipeline == "hybrid":
        query_vector = embeddings.embed_query(query)
        dense = vector_store.search(query_vector, fetch_k)
        sparse = bm25_store.search(query, fetch_k, region=region)
        return _rrf_fuse(dense, sparse, fetch_k)

    if pipeline != "legacy":
        raise ValueError(
            f"RAG_PIPELINE 값이 잘못되었습니다: {pipeline!r} "
            "(legacy, langchain, bm25, hybrid)"
        )

    query_vector = embeddings.embed_query(query)
    return vector_store.search(query_vector, fetch_k)


def _rrf_fuse(dense: list[dict], sparse: list[dict], top_k: int) -> list[dict]:
    """RRF(Reciprocal Rank Fusion)로 두 랭킹을 합친다.

        score(d) = Σ 1 / (K + rank_i(d))

    점수를 직접 더하지 않고 **순위만** 쓰는 이유:
    코사인 유사도(0~1)와 BM25 raw(0~20+)는 스케일이 달라 정규화 없이는
    더할 수 없고, 정규화 방식에 따라 결과가 흔들린다. 순위는 스케일이
    없으므로 이 문제를 통째로 피한다.

    ⚠️ 그 대가로 융합 점수는 **관련도를 뜻하지 않는다.** 무관한 질문에서도
       1위 문서는 최댓값을 받는다. 그래서 임계값 판정은 여기서 보존한
       vector_score / bm25_score 로 한다 — rag/scoring.py 참고.
    """
    k = scoring.rrf_k()
    merged: dict[str, dict] = {}

    for results, score_field in ((dense, "vector_score"), (sparse, "bm25_score")):
        for rank, item in enumerate(results, 1):
            key = f"{item.get('document_id')}::{item.get('chunk_index')}"
            entry = merged.get(key)
            if entry is None:
                entry = dict(item)
                entry["rrf_score"] = 0.0
                merged[key] = entry
            entry[score_field] = float(item.get("score", 0.0))
            entry["rrf_score"] += 1.0 / (k + rank)

    fused = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
    for item in fused:
        item["rrf_score"] = round(item["rrf_score"], 6)
        item["score"] = item["rrf_score"]
    return fused[:top_k]
```

## A-4. `rebuild_index()` 에 BM25 색인 추가 (378~381행)

**찾기**

```python
    vectors = embeddings.embed_documents([c["content"] for c in chunks])
    count = vector_store.rebuild(chunks, vectors, embeddings.get_dimension())
```

**바꾸기**

```python
    vectors = embeddings.embed_documents([c["content"] for c in chunks])
    count = vector_store.rebuild(chunks, vectors, embeddings.get_dimension())

    # BM25 는 같은 chunks 로 만든다. 순서가 어긋나면 bm25_store.load() 가
    # corpus_size 불일치로 막는다. 임베딩 API 를 쓰지 않으므로 비용 0.
    bm25_store.rebuild(chunks)
```

그리고 같은 함수의 반환 dict 에 한 줄 추가합니다.

**찾기**

```python
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "pipeline": "legacy",
    }
```

**바꾸기**

```python
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "pipeline": settings.RAG_PIPELINE.lower(),
        "bm25_indexed": count,
    }
```

## A-5. `search()` — region 전달과 임계값 판정 (444~451행)

**찾기**

```python
    results = _retrieve(query, fetch_k)
```

**바꾸기**

```python
    results = _retrieve(query, fetch_k, region=region)
```

**찾기**

```python
    # 유사도 임계값 (환각 방지 1차 장치)
    results = [r for r in results if r.get("score", 0.0) >= min_score]
```

**바꾸기** ★ 이 부분이 hybrid 를 실제로 동작하게 만드는 곳입니다

```python
    # 유사도 임계값 (환각 방지 1차 장치)
    #
    # ⚠️ 파이프라인마다 score 스케일이 다르다. 예전엔 이 한 줄이
    #    r["score"] >= min_score 였는데, hybrid 의 score 는 RRF(순위
    #    역수의 합)라 이론상 최댓값이 2/(RRF_K+1) = 0.0328 이다.
    #    RAG_MIN_SCORE(0.36)와 비교하면 전부 탈락해 모든 질문이
    #    "관련 정보를 찾을 수 없습니다" 로 나간다. 반대로 bm25 의 raw
    #    score 는 상한이 없어 0.36 으로는 아무것도 못 걸러 환각 방지가
    #    무력화된다. 둘 다 에러 없이 조용히 일어난다.
    results = scoring.filter_by_threshold(results, min_score, settings.RAG_PIPELINE)
```

## 건드리지 않는 것

- **`_apply_quota()`** — 3차에서 실측 검증된 설계입니다
  (region_specific 42.9% → 71.4%). 손대지 마십시오.
- **`ask()` 의 근거 0건 분기** — 자료없음 대응률 100% 의 근원입니다.
- **`_annotate_law_status()` / `_annotate_apartment_meta()`** — 무관합니다.

---

# B. `rag/llm.py` — LLM 토큰 계측

## B-1. import (14행)

**찾기**

```python
from django.conf import settings
```

**바꾸기**

```python
from django.conf import settings

from . import usage
```

## B-2. `_generate_gemini()` 안

**찾기**

```python
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
        return (response.text or "").strip() or None
```

**바꾸기**

```python
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
        prompt_tokens, output_tokens = usage.from_gemini(response)
        usage.record("llm", settings.GEMINI_MODEL, prompt_tokens, output_tokens)
        return (response.text or "").strip() or None
```

## B-3. `_generate_openai()` 안

**찾기**

```python
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return (response.choices[0].message.content or "").strip() or None
```

**바꾸기**

```python
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        prompt_tokens, output_tokens = usage.from_openai_chat(response)
        usage.record("llm", settings.OPENAI_MODEL, prompt_tokens, output_tokens)
        return (response.choices[0].message.content or "").strip() or None
```

---

# C. `rag/embeddings.py` — 임베딩 토큰 계측

## C-1. import

파일 상단의 `from django.conf import settings` 아래에 추가합니다.

```python
from . import usage
```

## C-2. `_embed_api_cached()` — 캐시 적중 계측

**찾기**

```python
    hit = len(texts) - len(missing)
    if hit:
        print(f"    캐시 재사용 {hit}개 / 신규 {len(missing)}개")
```

**바꾸기**

```python
    hit = len(texts) - len(missing)
    if hit:
        print(f"    캐시 재사용 {hit}개 / 신규 {len(missing)}개")
        # 캐시 적중은 API 호출이 없으므로 비용 0. 절감분을 눈에 보이게 센다.
        usage.record_cache_hit(hit)
```

## C-3. `_embed_openai()` — 배치마다 토큰 적립

**찾기**

```python
        # index 순서가 보장되지 않을 수 있어 정렬 후 사용
        vectors.extend(
            item.embedding for item in sorted(response.data, key=lambda d: d.index)
        )
```

**바꾸기**

```python
        # index 순서가 보장되지 않을 수 있어 정렬 후 사용
        vectors.extend(
            item.embedding for item in sorted(response.data, key=lambda d: d.index)
        )
        usage.record(
            "embedding",
            settings.OPENAI_EMBEDDING_MODEL,
            usage.from_openai_embedding(response),
        )
```

Gemini 임베딩도 쓰신다면 `_embed_gemini()` 의 응답 처리부에 같은 방식으로
`usage.record("embedding", settings.GEMINI_EMBEDDING_MODEL, ...)` 를 넣으면
됩니다. Gemini 임베딩 응답은 SDK 버전에 따라 usage 필드가 없을 수 있어
기본 패치에서는 뺐습니다.

---

# D. `config/settings.py`

`RAG_PIPELINE = os.getenv("RAG_PIPELINE", "legacy")` (257행) 아래에 추가합니다.

```python
# RAG_PIPELINE 선택지
#   legacy    : dense(FAISS) 단독 — 3차부터의 기본 경로
#   langchain : LangChain FAISS 래퍼
#   bm25      : BM25(sparse) 단독
#   hybrid    : dense + BM25 를 RRF 로 융합
#
# ⚠️ bm25/hybrid 로 바꾸면 반드시 재색인해야 합니다 (bm25.pkl 생성).
#     python manage.py seed_docs --reindex

# RRF 상수. 클수록 상위 순위의 영향이 완만해진다.
# 60 은 원논문 기본값(랭커가 여러 개인 대규모 코퍼스 기준)이며,
# 랭커 2개·청크 900개 규모에서는 10~30 이 더 날카로울 수 있다.
# 재색인 없이 바꿔 실험할 수 있으므로 스윕해 볼 것.
RAG_RRF_K = _env_int("RAG_RRF_K", 60)

# BM25 스케일 임계값. RAG_MIN_SCORE(코사인 0~1)와 스케일이 다르므로
# 별도 값이 필요하다. 0.0 은 무필터이며, 실제 값은
# manage.py measure_threshold 로 측정해서 정할 것.
RAG_MIN_SCORE_BM25 = _env_float("RAG_MIN_SCORE_BM25", 0.0)

# BM25 파라미터. b 는 문서 길이 정규화 강도(0~1).
# 이 코퍼스는 법령 조문(길다)과 품목 블록(짧다)이 섞여 길이 편차가 크므로
# 0.4~0.9 를 훑어볼 가치가 있다. 바꾸면 재색인 필요.
RAG_BM25_K1 = _env_float("RAG_BM25_K1", 1.5)
RAG_BM25_B = _env_float("RAG_BM25_B", 0.75)

# 토큰 단가 (USD / 1M tokens). rag/usage.py 의 기본 표를 덮어쓴다.
# 공식 가격이 바뀌면 여기서만 고치면 된다.
# 표에 없는 모델은 토큰만 세고 비용은 None 으로 나온다(0 으로 채우지 않음).
USAGE_PRICING: dict[str, dict[str, float]] = {}
```

`.env` 예시

```
RAG_PIPELINE=hybrid
RAG_RRF_K=60
RAG_MIN_SCORE_BM25=0.0
```

---

# E. 적용 후 확인

```bash
# 1. 토크나이저가 조사를 떼는가
python -c "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from rag.tokenizer import tokenize,tokenizer_name;print(tokenizer_name());print(tokenize('종이컵은 어떻게 버려요?'));print(tokenize('종이컵을 헹군 뒤 배출합니다'))"
```

양쪽에 `종이컵` 이 공통으로 나와야 합니다.

```bash
# 2. 재색인 (bm25.pkl 생성)
python manage.py seed_docs --reindex

# 3. hybrid 가 실제로 결과를 내는가 — 패치 A-5 없이는 0 이 나옵니다
python -c "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from django.test import override_settings;from rag import service
with override_settings(RAG_PIPELINE='hybrid'):
    print(len(service.search('종이컵은 어떻게 버리나요?', region='seoul')))"

# 4. 자료없음 대응이 살아 있는가 (3차 지표 100%)
python -c "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from rag import service;print(service.ask('오늘 날씨 어때?')['answer'][:60])"

# 5. 비용 계측
python -c "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from rag import service,usage
with usage.collect() as m:
    service.ask('종이컵은 어떻게 버리나요?', region='seoul')
print(m.as_dict())"

# 6. 파이프라인 비교 + 비용
python -m evals.run_search_eval
```

4번이 "관련 정보를 찾을 수 없습니다" 로 나와야 합니다. 답변이 나오면
`RAG_MIN_SCORE_BM25` 가 너무 낮은 것이므로 6번 출력의 임계값 섹션을 보고
올리십시오.

---

# F. 3차 자산과의 관계

| 파일 | 상태 |
|---|---|
| `evals/run_eval_hybrid.py` | **무수정.** AST 동일성 검증 대상 |
| `evals/run_report.py` | **무수정** |
| `rag/management/commands/measure_threshold.py` | **무수정** |
| `evals/qa_set.json` | **무수정** (30문항) |

`run_search_eval.py` 를 별도 파일로 만든 것은 이 때문입니다.
`run_eval_hybrid.py` 는 답변 채점(L3), `run_search_eval.py` 는
검색 채점(L1/L2)으로 층이 다릅니다.

단, `RAG_PIPELINE` 을 바꾸면 `run_eval_hybrid.py` 의 지표도 달라지므로,
**3차 수치와 비교할 때는 반드시 `RAG_PIPELINE=legacy` 로 두고 재측정**한
뒤에 hybrid 를 켜서 비교하십시오. 이식 검증과 개선 실험을 한 번에
섞으면 어느 쪽 변화인지 구분할 수 없습니다.
