"""단지 컨텍스트 조회를 함수 하나로 감싼다 (이음매 ①).

뷰·서비스 어디서도 Membership 을 직접 쿼리하지 말고 이 함수를 거칩니다.
이유: 사용자가 여러 단지에 소속될 수 있으므로(위탁관리 등) "지금 이
요청이 어느 단지 컨텍스트인가"를 정하는 규칙이 한 곳에만 있어야
rag/service.py · chat/views.py · apartments/views.py 가 같은 답을 얻습니다.

우선순위: 세션에 명시적으로 고른 단지 → is_primary 승인 소속 →
가장 먼저 승인된 소속. 세 경우 모두 없으면 None (미가입).
"""
from __future__ import annotations

SESSION_KEY = "active_apartment_id"


def _approved_memberships(user):
    from .models import Membership

    if not getattr(user, "is_authenticated", False):
        return Membership.objects.none()
    return Membership.objects.filter(member=user, status=Membership.Status.APPROVED)


def _resolve(memberships, request):
    """세션 선택 -> is_primary -> 최초 신청순으로 memberships 큐리셋 하나를
    좁혀 나간다. current_membership() 과 current_membership_for_chat() 이
    "어느 큐리셋(승인만 / 승인+신청)을 볼지"만 다르고 우선순위 규칙은
    똑같아야 하므로 이 함수 하나로 합친다."""
    if not memberships.exists():
        return None

    active_id = request.session.get(SESSION_KEY)
    if active_id:
        m = memberships.filter(apartment_id=active_id).first()
        if m:
            return m
        # 세션에 남은 값이 이 큐리셋 기준으로 더 이상 유효하지 않으면
        # (해지 등) 정리한다. current_membership_for_chat() 에서 먼저
        # 호출돼 지워지면 current_membership() 에도 영향을 주지만,
        # 애초에 승인 큐리셋에서도 무효했을 값이라 문제되지 않는다.
        request.session.pop(SESSION_KEY, None)

    primary = memberships.filter(is_primary=True).order_by("applied_at").first()
    if primary:
        return primary

    return memberships.order_by("applied_at").first()


def current_membership(request):
    """지금 컨텍스트의 승인된 Membership 행. 없으면 None.

    커뮤니티 접근, 단지 규정 열람·제안, 관리자 승인 큐 등 "승인된 소속"이
    실제로 필요한 모든 곳이 이 함수를 쓴다."""
    return _resolve(_approved_memberships(request.user), request)


def current_apartment_id(request) -> int | None:
    membership = current_membership(request)
    return membership.apartment_id if membership else None


def current_apartment(request):
    membership = current_membership(request)
    return membership.apartment if membership else None


def _chat_eligible_memberships(user):
    """챗봇 전용 완화 큐리셋. design 변경(2R-2): 아직 승인되지 않았어도
    "그 단지에 산다/관리한다"고 신청(requested)했다면 그 단지 규정을
    답변 근거로 써도 된다 — 거절(rejected)·해지(terminated)는 소속을
    주장할 근거가 사라졌으므로 제외한다."""
    from .models import Membership

    if not getattr(user, "is_authenticated", False):
        return Membership.objects.none()
    return Membership.objects.filter(
        member=user,
        status__in=[Membership.Status.REQUESTED, Membership.Status.APPROVED],
    )


def current_membership_for_chat(request):
    """챗봇 전용: 승인 여부와 무관하게 신청한 단지 규정을 근거로 쓴다.
    커뮤니티·규정 관리 등 쓰기/열람 권한이 걸린 화면은 절대 이 함수를
    쓰지 않는다 — 반드시 current_membership() (승인 필수) 을 쓴다."""
    return _resolve(_chat_eligible_memberships(request.user), request)


def current_apartment_id_for_chat(request) -> int | None:
    membership = current_membership_for_chat(request)
    return membership.apartment_id if membership else None


def set_active_apartment(request, apartment_id: int) -> None:
    """사용자가 명시적으로 단지를 전환할 때(여러 단지 소속 시) 쓴다."""
    request.session[SESSION_KEY] = apartment_id
