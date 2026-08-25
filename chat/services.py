"""질문 클러스터 매칭.

3차 app/routers/rag.py 안에 있던 _assign_cluster() 를 옮겨온 자리입니다.
라우터(HTTP 처리)에 임베딩 호출과 numpy 연산이 들어있는 구조였는데,
서비스 계층으로 분리합니다.

로직 자체는 3차 그대로입니다.
    질문 임베딩 1회 → 기존 클러스터 벡터와 코사인 비교 →
    settings.QUESTION_CLUSTER_THRESHOLD 이상이면 편입, 미만이면 새로 생성
"""
from __future__ import annotations

import json

from django.conf import settings

from rag.models import QuestionCluster


def assign_cluster(question: str) -> QuestionCluster | None:
    """질문을 기존 클러스터에 매칭하거나 새 클러스터를 만듭니다.

    3차 대비 달라진 점: db 세션 파라미터가 없어지고, 임계값이
    하드코딩(0.85)에서 settings.QUESTION_CLUSTER_THRESHOLD 로 올라갔습니다.
    """
    import numpy as np

    from rag import embeddings

    try:
        vec = embeddings.embed_documents([question])[0]
    except Exception:
        # 임베딩 실패가 질문 처리 전체를 막지 않도록 3차와 같이 None 반환.
        return None

    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm

    best, best_sim = None, -1.0
    for cluster in QuestionCluster.objects.all():
        c_vec = np.asarray(json.loads(cluster.embedding), dtype=np.float32)
        sim = float(np.dot(arr, c_vec))
        if sim >= settings.QUESTION_CLUSTER_THRESHOLD and sim > best_sim:
            best, best_sim = cluster, sim

    if best is not None:
        # F() 표현식을 쓰면 동시 요청에서 카운트가 유실되지 않습니다.
        from django.db.models import F

        QuestionCluster.objects.filter(pk=best.pk).update(count=F("count") + 1)
        return best

    return QuestionCluster.objects.create(
        representative=question,
        embedding=json.dumps(arr.tolist()),
        count=1,
    )
