"""챗봇 View.

3차 app/routers/rag.py 의 라우터를 옮기면서 세 책임을 분리했습니다.
    1) 대화 저장·복원     → 이 파일 (Django ORM)
    2) 검색·답변 생성     → rag/service.py 의 ask()
    3) 질문 클러스터 매칭 → chat/services.py 의 assign_cluster()

■ 응답 JSON 형식은 3차와 동일하게 유지
    static/js/chat.js(3차 app.js 이식본)가 fetch 경로와 CSRF 헤더만
    바꾸고 파싱 코드를 재사용할 수 있게, 3차 라우터의 응답 키를
    그대로 씁니다.
        ask      → {session_id, answer, tip, source, sources}
        sessions → [{session_id, region, messages: [...]}, ...]
        popular  → [{question, count}, ...]

■ 3차와 달라진 것
    - session_id: JS Date.now() 문자열 → ChatSession pk (문자열로 직렬화).
      ask 에 session_id=null 로 오면 서버가 대화방을 만들어 돌려줍니다.
      3차의 LEGACY_SESSION_KEY(NULL 세션 묶음) 예외 처리가 사라집니다.
    - 대화방 삭제가 실제로 서버에서 지워집니다 (3차는 화면에서만 지워져
      새로고침하면 되살아났습니다).
"""
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from members.models import REGION_CHOICES
from rag import service
from rag.models import QuestionCluster

from .models import ChatLog, ChatMessage, ChatSession
from .services import assign_cluster


def _json(payload, status=200):
    return JsonResponse(
        payload, status=status, safe=False, json_dumps_params={"ensure_ascii": False}
    )


def _read_json(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


# 화면 드롭다운에 보여줄 지역 (전국 공통은 문서 속성이지 선택지가 아님)
SELECTABLE_REGIONS = [(code, label) for code, label in REGION_CHOICES if code != "common"]
_VALID_REGIONS = {code for code, _ in SELECTABLE_REGIONS}


class ChatRoomView(LoginRequiredMixin, View):
    """챗봇 화면. 대화 데이터는 chat.js 가 chat:sessions 로 복원합니다.

    3차의 initChat() 이 /api/me 로 받아오던 사용자 정보(이름·이메일·
    관리자 여부)는 템플릿에서 request.user 로 직접 렌더링합니다 —
    /api/me 왕복이 사라집니다.
    """

    def get(self, request):
        from apartments import permissions as apt_permissions, scope as apt_scope

        # 사이드바 단지명: 미승인(신청 중)이어도 표시
        chat_membership = apt_scope.current_membership_for_chat(request)
        chat_apartment = chat_membership.apartment if chat_membership else None
        # 커뮤니티 접근·관리 권한: 승인된 소속 기준
        approved_membership = apt_scope.current_membership(request)

        return render(
            request,
            "chat/room.html",
            {
                "regions": SELECTABLE_REGIONS,
                "default_region": request.user.region if request.user.region in _VALID_REGIONS else "seoul",
                "can_access_community": apt_permissions.has_community_access(request),
                # 4차 UI 리디자인: 사이드바에 단지명·역할 표시 (신청 중도 보임)
                "apartment_name": str(chat_apartment) if chat_apartment else None,
                "membership_role": chat_membership.get_role_display() if chat_membership else None,
                "is_manager": bool(approved_membership and approved_membership.role == "manager"),
            },
        )


class ChatAskView(LoginRequiredMixin, View):
    """질문을 받아 답변을 생성하고 대화 기록에 저장합니다. 3차 POST /api/chat 대응.

    처리 순서 (3차 라우터와 동일):
        1. 대화방 확보 — session_id 가 없으면 새로 만든다
        2. 직전 대화 CHAT_HISTORY_TURNS*2 개를 히스토리로 조회
           (지금 질문은 프롬프트에 별도로 들어가므로 저장 전에 조회)
        3. 사용자 질문을 ChatMessage 로 저장
        4. rag.service.ask(question, owner_id, region, history)
        5. ChatLog(통계) + 클러스터 매칭 — 실패해도 답변은 나가야 하므로
           클러스터 실패는 무시 (assign_cluster 가 None 반환)
        6. 챗봇 답변을 ChatMessage 로 저장 (tip, sources 포함)
    """

    def post(self, request):
        body = _read_json(request)

        question = (body.get("question") or "").strip()
        if not question:
            return _json({"detail": "question 이 필요합니다."}, status=400)

        region = body.get("region") or request.user.region
        if region not in _VALID_REGIONS:
            region = "seoul"

        # 4차 2R 추가분: 단지 컨텍스트. 대화방을 새로 만들 때만 고정하고
        # (기존 대화방은 옛 기준으로 남는다 — 설계 문서 10절), 검색·답변
        # 생성에는 매 질문마다 최신 값을 넘긴다(단지를 옮긴 뒤 새로 하는
        # 질문은 새 단지 기준이어야 하므로).
        # design 변경(2R-2): 챗봇은 승인 여부와 무관하게 신청한 단지의
        # 규정까지 답변 근거로 쓴다 — 커뮤니티/규정 관리 등 승인이
        # 진짜로 필요한 화면과 달리, 자기가 신청한 단지 정보를 물어보는
        # 것 자체는 검증 전이어도 막을 이유가 없다는 판단.
        from apartments import scope

        apartment_id = scope.current_apartment_id_for_chat(request)

        # ── 1. 대화방 확보 ──
        session_id = body.get("session_id")
        if session_id:
            session = get_object_or_404(
                ChatSession, pk=session_id, owner=request.user
            )
            # 드롭다운에서 지역을 바꾼 채 질문하면 대화방 지역도 따라간다.
            if session.region != region:
                session.region = region
        else:
            session = ChatSession(owner=request.user, region=region, apartment_id=apartment_id)
        session.title = session.title or question[:30]
        session.save()

        # ── 2. 히스토리 조회 (방금 질문 제외 — 저장 전에 읽는다) ──
        from django.conf import settings

        turns = max(0, settings.CHAT_HISTORY_TURNS)
        history = [
            {"role": m.role, "content": m.content}
            for m in reversed(
                session.messages.order_by("-created_at", "-id")[: turns * 2]
            )
        ]

        # ── 3. 사용자 질문 저장 ──
        ChatMessage.objects.create(
            session=session, role=ChatMessage.Role.USER, content=question
        )

        # ── 4. 답변 생성 ──
        result = service.ask(
            question,
            owner_id=request.user.pk,
            region=region,
            history=history or None,
            apartment_id=apartment_id,
        )

        # ── 5. 통계 로그 + 클러스터 ──
        log = ChatLog.objects.create(
            user=request.user,
            question=question,
            region=region,
            # 근거를 하나라도 찾았는지. '자료없음 대응률' 지표의 원천.
            has_answer=bool(result.get("sources")),
            apartment_id=apartment_id,
        )
        cluster = assign_cluster(question)
        if cluster is not None:
            log.cluster = cluster
            log.save(update_fields=["cluster"])

        # ── 6. 답변 저장 ──
        bot_msg = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.ASSISTANT,
            content=result.get("answer", ""),
            tip=result.get("tip", "") or "",
            sources=result.get("sources", []) or [],
            # 4차 추가분: 유사 질문 추천 · 법령 시행 안내
            suggested_questions=result.get("suggested_questions", []) or [],
            law_notice=result.get("law_notice", "") or "",
            contact_cards=result.get("contact_cards", []) or [],
        )
        session.save(update_fields=["updated_at"])  # 목록 정렬 갱신

        return _json(
            {
                "session_id": str(session.pk),
                "message_id": bot_msg.pk,
                "answer": result.get("answer", ""),
                "tip": result.get("tip", ""),
                "source": result.get("source", ""),
                "sources": result.get("sources", []),
                "suggested_questions": result.get("suggested_questions", []),
                "law_notice": result.get("law_notice", ""),
                "contact_cards": result.get("contact_cards", []),
            }
        )


class ChatSessionListView(LoginRequiredMixin, View):
    """대화방 목록 + 각 대화 복원. 3차 GET /api/chat/sessions 대응.

    3차는 전체 ChatMessage 를 읽어 파이썬에서 session_id 로 그룹핑했지만
    FK 가 생겼으므로 prefetch 한 번이면 됩니다. 최신 활동 순
    (ChatSession.Meta.ordering = -updated_at)으로 내려가 3차 JS 의
    "첫 번째 = 현재 대화" 가정이 그대로 성립합니다.
    """

    def get(self, request):
        sessions = ChatSession.objects.filter(owner=request.user).select_related(
            "apartment"
        ).prefetch_related("messages")

        payload = []
        for s in sessions:
            payload.append(
                {
                    "session_id": str(s.pk),
                    "region": s.region,
                    # 4차 2R 추가분: 이 대화방이 고정한 단지 컨텍스트.
                    # 프로필/단지를 바꾼 뒤에도 옛 대화방은 이 값으로 남는다
                    # (설계 문서 10절) — 프론트가 배지로 보여줘 혼란을 줄인다.
                    "apartment": s.apartment.name if s.apartment_id else None,
                    "messages": [
                        {
                            "role": m.role,
                            "content": m.content,
                            "tip": m.tip,
                            "sources": m.sources,
                            # 3차 JS 는 source(제목 문자열)가 없으면
                            # sources 로 조립하므로 안 보내도 되지만,
                            # 파싱 분기를 줄이기 위해 만들어 보낸다.
                            "source": ", ".join(
                                dict.fromkeys(
                                    x.get("title", "") for x in (m.sources or [])
                                )
                            ),
                            # 4차 추가분: 새로고침 후 복원에도 같이 실어 보낸다.
                            "suggested_questions": m.suggested_questions,
                            "law_notice": m.law_notice,
                            "contact_cards": m.contact_cards,
                            "feedback": m.feedback,
                            "message_id": m.pk,
                            "created_at": m.created_at.isoformat(),
                        }
                        for m in s.messages.all()
                    ],
                }
            )
        return _json(payload)


class ChatSessionDeleteView(LoginRequiredMixin, View):
    """대화방 삭제. 메시지는 FK CASCADE 로 함께 지워집니다.

    ChatLog(통계)는 대화방과 무관하게 남습니다 — 대시보드 수치가
    대화방 삭제로 줄어들면 통계가 아니게 됩니다.
    """

    def post(self, request, pk):
        session = get_object_or_404(ChatSession, pk=pk, owner=request.user)
        session.delete()
        return _json({"deleted": True})


class ChatFeedbackView(LoginRequiredMixin, View):
    """답변 피드백(좋아요/싫어요) 저장. POST 전용."""

    def post(self, request, pk):
        import json
        from django.utils import timezone

        message = get_object_or_404(
            ChatMessage, pk=pk, session__owner=request.user, role=ChatMessage.Role.ASSISTANT,
        )
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _json({"error": "invalid body"}, status=400)

        feedback = body.get("feedback")
        if feedback not in ("positive", "negative", None):
            return _json({"error": "invalid feedback value"}, status=400)

        message.feedback = feedback
        message.feedback_at = timezone.now() if feedback else None
        message.save(update_fields=["feedback", "feedback_at"])
        return _json({"ok": True, "feedback": feedback})


class PopularQuestionView(LoginRequiredMixin, View):
    """인기 질문 TOP N. 3차 GET /api/popular-questions 대응.

    환영 화면의 빠른 질문 버튼을 채웁니다. 클러스터가 없으면(초기 상태)
    프론트가 기본 질문 4개를 그대로 쓰므로 빈 배열을 돌려줘도 됩니다.
    """

    def get(self, request):
        try:
            limit = max(1, min(int(request.GET.get("limit", 5)), 20))
        except ValueError:
            limit = 5
        clusters = QuestionCluster.objects.order_by("-count")[:limit]
        return _json(
            [{"question": c.representative, "count": c.count} for c in clusters]
        )
