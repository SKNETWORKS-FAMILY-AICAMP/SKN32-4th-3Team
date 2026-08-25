"""쓰기·검토 권한 판정을 한 곳에 모은다 (이음매 ③).

자기신고(입주민 코드 인증)로 만들어진 데이터가 쓰기 권한으로 저절로
승격되지 않게 하는 것이 이 모듈의 목적입니다. 관리사무소 관리자
Membership 은 항상 서비스 운영자가 승인한 것만 True 로 판정합니다.
"""
from __future__ import annotations


def is_apartment_manager(user, apartment_id: int) -> bool:
    from .models import Membership

    if not getattr(user, "is_authenticated", False) or apartment_id is None:
        return False
    return Membership.objects.filter(
        member=user, apartment_id=apartment_id,
        role=Membership.Role.MANAGER, status=Membership.Status.APPROVED,
    ).exists()


def can_manage_apartment(user, apartment_id: int) -> bool:
    """이 단지에 대해 검토·승인 권한이 있는지. 서비스 운영자는 모든 단지,
    관리사무소 관리자는 자기 단지만. 단지 규정 승인, 입주민 신청 승인이
    모두 이 판정 하나를 공유한다 — 승인자가 "이 단지를 실제로 아는
    사람"이어야 한다는 조건이 둘 다 같기 때문이다."""
    if getattr(user, "is_service_admin", False):
        return True
    return is_apartment_manager(user, apartment_id)


# 하위 호환 별칭. 단지 규정 검토 호출부는 이 이름을 계속 쓴다.
can_review_rule = can_manage_apartment


def can_submit_official_rule(user, apartment_id: int) -> bool:
    """source_level=official 로 규정을 올릴 수 있는지. 입주민은 항상
    resident 등급으로만 제안할 수 있다 — 자기신고 계정이 official 등급을
    자칭하면 사실상 검토 없이 official 로 노출되는 우회가 생긴다."""
    return can_manage_apartment(user, apartment_id)


def has_community_access(request) -> bool:
    """커뮤니티(게시판) 접근 가능 여부. design 변경(2R-2): 지금까지 게시판은
    로그인만 하면(비로그인도 열람은) 열려 있었지만, 이제는 승인된 단지
    소속이 있어야 들어갈 수 있다 — 서비스 운영자는 자기 소속 없이도
    전역 접근이 가능하다(dashboard.AdminRequiredMixin 과 같은 결)."""
    from . import scope

    user = request.user
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_service_admin", False):
        return True
    return scope.current_membership(request) is not None


def can_access_apartment_community(user, apartment_id) -> bool:
    """이 글이 속한 "그 단지" 커뮤니티에 실제로 들어갈 자격이 있는지.
    has_community_access() 는 "승인된 소속이 하나라도 있는가"만 보는
    목록 진입 게이트이고, 이건 상세/수정/삭제처럼 특정 단지 글 하나에
    대한 객체 단위 검사다 — 다른 단지 글의 URL을 직접 쳐서 들어오는
    것까지 막으려면 이 검사가 따로 필요하다."""
    if getattr(user, "is_service_admin", False):
        return True
    if apartment_id is None or not getattr(user, "is_authenticated", False):
        return False
    from .models import Membership

    return Membership.objects.filter(
        member=user, apartment_id=apartment_id, status=Membership.Status.APPROVED,
    ).exists()


def managed_apartment_ids(user) -> list[int]:
    """이 사용자가 관리자로 승인된 단지 id 목록. 승인 큐 화면에서 쓴다."""
    from .models import Membership

    if getattr(user, "is_service_admin", False):
        from .models import Apartment

        return list(Apartment.objects.values_list("id", flat=True))
    if not getattr(user, "is_authenticated", False):
        return []
    return list(
        Membership.objects.filter(
            member=user, role=Membership.Role.MANAGER, status=Membership.Status.APPROVED,
        ).values_list("apartment_id", flat=True)
    )
