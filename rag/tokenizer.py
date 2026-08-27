# path : rag/tokenizer.py
"""
[RAG 파트] BM25 색인·질의용 한국어 토크나이저.

색인과 질의가 반드시 **같은 함수**를 쓰도록 한 곳에 모읍니다.
두 쪽이 다르게 잘리면 검색이 에러 없이 조용히 망가집니다.

왜 형태소 분석이 필요한가
─────────────────────────
BM25 는 벡터 검색과 달리 **정확한 단어 일치**가 전부입니다.
공백/문자 단위로만 자르면 조사가 붙은 채 색인됩니다.

    질의 "종이컵은 어떻게 버려요?"  ->  ['종이컵은', '어떻게', '버려요']
    문서 "종이컵을 헹군 뒤 배출"     ->  ['종이컵을', '헹군', '배출']
                                          ^^^^^^^ 서로 다른 토큰

'종이컵은' != '종이컵을' 이므로 핵심 명사가 매칭되지 않습니다.
한국어에서 BM25 도입은 "라이브러리를 붙인다"로 끝나지 않고
토크나이저가 세트입니다.

kiwipiepy 가 있으면 형태소 기반, 없으면 조사 제거 폴백으로 동작합니다.

    pip install kiwipiepy
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")

# 색인에 남길 형태소 태그.
#   N* 명사류 / V* 용언 어간 / SL 외국어 / SN 숫자 / SH 한자 / XR 어근
#
# MAG(부사)는 일부러 뺐습니다. "어떻게", "어디", "왜" 같은 질문 어휘가
# 모든 안내 문서에 골고루 매칭돼 변별력을 떨어뜨립니다.
_KEEP_TAGS = (
    "NNG", "NNP", "NNB", "NR", "NP",
    "VV", "VA", "VX",
    "SL", "SN", "SH",
    "XR",
)

# 이 프로젝트 도메인의 복합명사. kiwi 가 한 덩어리로 잡으면
# 법령 표기("음식물류 폐기물")와 가이드 표기("음식물쓰레기")가
# 영원히 매칭되지 않으므로 구성 명사를 함께 색인합니다.
_COMPOUND_EXPANSION = {
    "음식물쓰레기": ("음식물", "쓰레기"),
    "종량제봉투": ("종량제", "봉투"),
    "대형폐기물": ("대형", "폐기물"),
    "생활폐기물": ("생활", "폐기물"),
    "재활용품": ("재활용",),
    "분리배출": ("분리", "배출"),
    "폐의약품": ("의약품",),
    "폐건전지": ("건전지",),
    "폐형광등": ("형광등",),
    "투명페트병": ("투명", "페트병"),
    "일회용품": ("일회용",),
    "무상방문수거": ("무상", "방문", "수거"),
    "클린하우스": ("클린", "하우스"),
    "자동크린넷": ("크린넷",),
}

# 폴백용 조사/어미 (긴 것부터 시도)
_SUFFIXES = (
    "에서는", "으로는", "에게서", "으로써", "으로서", "이라고", "에서도", "까지도",
    "부터는", "에게는", "한테는",
    "에서", "에게", "께서", "으로", "이라", "라고", "처럼", "보다", "까지",
    "부터", "마다", "조차", "밖에", "한테", "이나", "라도",
    "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "로",
    "만", "랑", "야", "요",
)

_kiwi = None
_TOKENIZER_NAME = "regex-fallback"


def _get_kiwi():
    """kiwipiepy 인스턴스를 지연 로딩한다. 없으면 None."""
    global _kiwi, _TOKENIZER_NAME
    if _kiwi is not None:
        return _kiwi
    try:
        from kiwipiepy import Kiwi
    except ImportError:
        return None
    _kiwi = Kiwi()
    _TOKENIZER_NAME = "kiwi"
    return _kiwi


def tokenizer_name() -> str:
    """현재 토크나이저 이름. 인덱스 payload 에 기록해 불일치를 잡는다."""
    _get_kiwi()
    return _TOKENIZER_NAME


def _expand(token: str) -> list[str]:
    """복합명사면 구성 명사를 함께 돌려준다."""
    extra = _COMPOUND_EXPANSION.get(token)
    return [token, *extra] if extra else [token]


def _fallback_tokenize(text: str) -> list[str]:
    """형태소 분석기가 없을 때: 조사를 떼되 원형도 함께 남긴다.

    '고양이'에서 '이'를 떼면 '고양'이 되는 오절단이 나므로,
    재현율을 지키기 위해 원형과 어간을 둘 다 색인한다.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = raw.lower()
        tokens.extend(_expand(token))

        if len(token) < 3:
            continue
        for suffix in _SUFFIXES:
            if not token.endswith(suffix):
                continue
            stem = token[: -len(suffix)]
            if len(stem) >= 2:
                tokens.extend(_expand(stem))
            break
    return tokens


def tokenize(text: str) -> list[str]:
    """검색용 토큰 목록. 색인·질의 양쪽에서 이 함수만 쓴다."""
    text = text or ""
    kiwi = _get_kiwi()
    if kiwi is None:
        return _fallback_tokenize(text)

    tokens: list[str] = []
    for token in kiwi.tokenize(text):
        if token.tag not in _KEEP_TAGS:
            continue
        form = token.form.lower()
        if len(form) < 2 and not form.isdigit():
            continue
        tokens.extend(_expand(form))
    return tokens
