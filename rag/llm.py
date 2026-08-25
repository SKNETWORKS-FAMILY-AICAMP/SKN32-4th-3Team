"""
LLM 연동 (Gemini / OpenAI 전환 가능).

- generate_summary(prompt) : 문서 요약
- answer_with_context(...)  : RAG 답변 생성

.env의 LLM_BACKEND 값으로 백엔드 전환:
  - "gemini" : Google Gemini API (기본)
  - "openai" : OpenAI API

    pip install google-genai openai
"""

from django.conf import settings


# ─────────────────── 공통 호출부 ───────────────────


def _generate(prompt: str) -> str | None:
    """LLM_BACKEND에 따라 Gemini 또는 OpenAI를 호출한다."""
    backend = settings.LLM_BACKEND.lower()
    if backend == "openai":
        return _generate_openai(prompt)
    return _generate_gemini(prompt)


def _generate_gemini(prompt: str) -> str | None:
    """Gemini를 호출한다."""
    if not settings.GEMINI_API_KEY:
        return None

    try:
        from google import genai
    except ImportError:
        print("[Gemini] google-genai 가 설치되지 않았습니다.  pip install google-genai")
        return None

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
        return (response.text or "").strip() or None
    except Exception as exc:
        msg = str(exc).lower()
        if "429" in msg or "quota" in msg or "rate" in msg or "resource exhausted" in msg:
            print(f"[Gemini] API 할당량 초과: {exc}")
            return "__QUOTA_EXCEEDED__"
        print(f"[Gemini] 호출 실패: {exc}")
        return None


def _generate_openai(prompt: str) -> str | None:
    """OpenAI를 호출한다."""
    if not settings.OPENAI_API_KEY:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        print("[OpenAI] openai 패키지가 설치되지 않았습니다.  pip install openai")
        return None

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception as exc:
        msg = str(exc).lower()
        if "429" in msg or "quota" in msg or "rate" in msg:
            print(f"[OpenAI] API 할당량 초과: {exc}")
            return "__QUOTA_EXCEEDED__"
        print(f"[OpenAI] 호출 실패: {exc}")
        return None


# ─────────────────── 요약 (기존) ───────────────────


def generate_summary(prompt: str) -> str:
    result = _generate(prompt)
    if result is None:
        return "(요약 기능 준비 중: GEMINI_API_KEY 를 설정하거나 google-genai 를 설치하세요)"
    return result


# ─────────────────── RAG 답변 (RAG 파트) ───────────────────

ANSWER_PROMPT = """당신은 환경·분리배출 관련 상담 도우미 'Ecobot' 입니다.
아래 [근거]에 제시된 문서 내용만 사용해서 한국어로 답변하세요.
 
반드시 아래 2개 섹션으로 나누어 답변하세요. 각 섹션은 정확히 해당 태그로 감싸세요.
 
<answer>가이드 문서와 법령 근거를 종합하여 질문에 대한 답변을 3~5문장으로 작성합니다. 가이드 내용(분리배출 방법, 지역 규정 등)과 법령 근거(법령명·조문 번호)가 있으면 자연스럽게 함께 서술하세요.</answer>
<tip>근거에 나온 내용 중 실천에 도움이 되는 부분을 골라 1~2문장으로 짚어줍니다.</tip>
 
작성 규칙
1. 근거에 없는 내용은 절대 추측하거나 지어내지 마세요.
2. 근거만으로 답할 수 없으면 "관련 정보를 찾을 수 없습니다."라고만 적으세요.
3. 태그 바깥에는 아무 텍스트도 쓰지 마세요.
4. [이전 대화]가 있다면 참고해서 자연스럽게 이어 답하되, 이전 대화 내용 자체를
   근거로 새로운 사실을 지어내지 마세요 (사실 판단은 항상 [근거]만 기준).
5. **근거의 섹션 제목·항목 분류를 반드시 확인하고 그대로 따르세요.**
   "일반쓰레기로 배출해야 하는 음식물", "음식물인데 일반쓰레기인 것들"
   같은 제목 아래 나열된 품목은 **일반쓰레기**로 답해야 합니다.
   "→ 일반쓰레기" 라고 명시된 품목도 반드시 일반쓰레기로 답하세요.
   품목명에 "음식물"이 포함되어 있더라도 분류 표기를 우선합니다.
   (예: 달걀/계란 껍질이 "일반쓰레기" 섹션에 있으면 일반쓰레기로 답해야 함)
6. **법령명과 조문 번호는 [근거]에 실제로 등장한 것만 인용하세요.**
   근거에 없는 법령명이나 조문 번호를 기억이나 추측으로 쓰면 안 됩니다.
   근거에 조문 번호가 없으면 법령을 인용하지 말고 가이드 내용만으로 답하세요.
7. **질문의 대상(품목·지역)이 근거의 대상과 다르면 적용하지 마세요.**
   비슷해 보여도 다른 품목이면 "관련 정보를 찾을 수 없습니다."라고 답해야 합니다.
   (예: 보조배터리·폐건전지 배출 안내를 전기차 폐배터리에 적용 금지)
8. 질문한 품목의 **일반적인 처리 방법을 먼저** 설명하고, 예외 사항은 그다음에 덧붙이세요.
   예외만 설명하고 일반 방법을 빠뜨리면 안 됩니다.
9. **tip 도 [근거]에 있는 내용만 쓰세요.**
   일반 상식이나 개인적인 요령을 덧붙이지 말고, 근거에서 실천에 도움이 되는
   부분을 골라 다시 짚어주는 방식으로 작성합니다.
   근거에 짚어줄 만한 내용이 없으면 tip 은 비워 두세요.
10. **[우리 단지 규정]이 [배출 가이드]·[관련 법령]과 함께 있으면 단지 규정을
    우선 적용하세요.** 단지 규정이 지자체 안내와 다른 내용이면(예: 구청은
    매일 배출, 단지는 수요일만) 단지 기준으로 답하고 지자체 기준과 다르다는
    점을 한 문장으로 짧게 덧붙이세요. [우리 단지 규정] 섹션이 근거에 없으면
    이 규칙은 무시하고 평소처럼 답하세요.

--- 예시 1 (기본) ---
[근거]
[근거 1 · [환경부 공통] 재활용품 분리배출 가이드라인]
[품목별 분리배출 요령 > 무색 투명 페트(PET)병]
- 내용물 깨끗이 비우고 부착상표(라벨) 제거 후 가능한 압착하여 뚜껑 닫아 배출
- 유색 페트병과 분리하여 별도 배출
 
[질문] 페트병 라벨 꼭 떼야 해?
<answer>네, 페트병은 내용물을 깨끗이 비우고 부착상표(라벨)를 제거한 뒤 배출해야 합니다. 가능하면 압착해서 뚜껑을 닫아 내놓고, 무색 투명 페트병은 유색 페트병과 분리해 별도로 배출합니다.</answer>
<tip>압착해서 뚜껑을 닫아 배출하면 부피가 줄고 이물질이 들어가지 않습니다.</tip>
 
--- 예시 2 (질문 대상이 근거와 다를 때 · 규칙 7) ---
[근거]
[근거 1 · [공통] 생활계 유해폐기물 배출 안내]
[폐건전지 > 배출 방법]
- 주민센터, 아파트 단지 등에 설치된 폐건전지 전용 수거함에 배출
- 보조배터리·전자담배 배터리 등 리튬 배터리는 화재 위험이 있으므로 전용 수거함에 배출
 
[질문] 전기차 폐배터리는 어디에 신고하나요?
<answer>관련 정보를 찾을 수 없습니다.</answer>
<tip></tip>
 
--- 예시 3 (근거에 조문 번호가 없을 때 · 규칙 6) ---
[근거]
[근거 1 · [환경부 공통] 재활용품 분리배출 가이드라인]
[분리배출 기본 원칙]
1. 비운다  2. 헹군다  3. 분리한다  4. 섞지 않는다
 
[질문] 분리배출은 법으로 정해진 의무인가요?
<answer>제공된 자료에는 분리배출의 기본 원칙만 담겨 있어 법적 의무 여부는 확인할 수 없습니다. 환경부 가이드라인은 내용물을 비우고 헹군 뒤 재질별로 분리해 섞지 않고 배출하도록 안내하고 있습니다. 법적 근거가 필요하시면 관할 지자체나 국가법령정보센터에서 확인해 주세요.</answer>
<tip>비우기·헹구기·분리하기·섞지 않기 네 가지만 지켜도 재활용률이 올라갑니다.</tip>
 
--- 예시 4 (섹션 제목의 하위 분류 따르기 · 규칙 5) ---
[근거]
[근거 1 · [천안시] 분리배출 가이드]
[천안시 음식물쓰레기 배출 요령 > 일반쓰레기로 배출해야 하는 음식물]
- 뼈다귀 (소, 돼지, 닭 등의 뼈)
- 알 껍질 (달걀, 메추리알)
- 견과류 껍질 (호두, 밤, 땅콩)
 
[질문] 달걀 껍질은 음식물쓰레기야?
<answer>아닙니다. 천안시 기준으로 달걀·메추리알 껍질은 일반쓰레기로 배출해야 하는 품목으로 분류되어 있어 종량제봉투에 버려야 합니다. 뼈다귀나 견과류 껍질도 마찬가지로 일반쓰레기입니다.</answer>
<tip>알 껍질·뼈·견과류 껍질은 음식물처럼 보여도 일반쓰레기이니 함께 모아 두면 편합니다.</tip>

--- 예시 5 (단지 규정이 지자체 안내와 다를 때 · 규칙 10) ---
[근거]
[근거 1 · 래미안OO - 배출시간]
[배출시간]
- 음식물쓰레기는 매주 수요일·토요일 저녁 7시~9시에만 배출 가능

[근거 2 · [서울시] 분리배출 가이드]
[음식물쓰레기 배출 요령]
- 음식물쓰레기는 매일 저녁 7시부터 자정까지 배출 가능

[질문] 음식물쓰레기 아무 때나 버려도 돼?
<answer>회원님 단지(래미안OO) 기준으로는 매주 수요일과 토요일 저녁 7시~9시에만 음식물쓰레기를 배출할 수 있습니다. 서울시 일반 안내는 매일 저녁 7시부터 자정까지지만, 단지 배출시간이 더 좁게 정해져 있으니 단지 규정을 따라야 합니다.</answer>
<tip>배출 요일을 놓치면 다음 배출일까지 기다려야 하니 수요일·토요일 저녁 시간을 기억해 두세요.</tip>

--- 실제 질문 ---
{history_block}[근거]
{context}
 
[질문] {question}
"""


import re

_SECTION_RE = re.compile(r"<(answer|tip)>(.*?)</\1>", re.DOTALL)


def _parse_sections(text: str) -> dict:
    """LLM 응답에서 <answer>, <tip> 섹션을 추출한다."""
    sections = {"answer": "", "tip": ""}
    for match in _SECTION_RE.finditer(text):
        sections[match.group(1)] = match.group(2).strip()
    return sections


def _format_history(history: list[dict] | None) -> str:
    """이전 대화를 프롬프트에 넣을 블록으로 변환. 없으면 빈 문자열(기존과 동일)."""
    if not history:
        return ""
    lines = [
        f"{'사용자' if turn.get('role') == 'user' else '챗봇'}: {turn.get('content', '')}"
        for turn in history
    ]
    return "[이전 대화]\n" + "\n".join(lines) + "\n\n"


def answer_with_context(question: str, context: str, history: list[dict] | None = None) -> dict:
    """검색된 문서를 근거로 2섹션 답변을 생성한다. (rag_service 가 호출)

    반환: {"answer": str, "tip": str}

    history: 최근 대화 목록. "대화 흐름 유지" 기능용으로 추가한 파라미터라
    안 넘기면(None) 기존과 완전히 동일하게 동작한다(하위 호환).
    """
    history_block = _format_history(history)
    prompt = ANSWER_PROMPT.format(history_block=history_block, context=context, question=question)
    result = _generate(prompt)

    if result == "__QUOTA_EXCEEDED__":
        return {
            "answer": "현재 API 사용량이 초과되었습니다. 잠시 후 다시 질문해 주세요.",
            "tip": "",
        }

    if result is None:
        backend = settings.LLM_BACKEND.lower()
        if backend == "openai":
            msg = "[LLM 미연결] OPENAI_API_KEY 설정 후 다시 질문하세요."
        else:
            msg = "[LLM 미연결] GEMINI_API_KEY 설정 후 다시 질문하세요."
        return {
            "answer": msg,
            "tip": "",
        }

    sections = _parse_sections(result)

    # 파싱 실패 시 전체 응답을 answer 에 넣는다
    if not any(sections.values()):
        sections["answer"] = result

    return sections