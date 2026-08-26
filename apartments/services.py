"""단지 규정 승인 → rag.Document 동기화.

design 문서 6절이 권장한 (A)안: ApartmentRule 원본을 그대로 색인하지 않고
승인된 규정만 Document 로 변환해 색인 파이프라인을 그대로 재사용한다.
rag/views.py:DocumentUploadView 가 "저장 후 rebuild_index() 호출" 하는
패턴을 그대로 따른다.

필터는 색인 적재 단계(rag/service.py:_load_from_db, Document.status)에만
건다 — search()/ask() 에 걸면 _apply_quota() 자리배분과 얽혀 기존
지표(통과율 93.3%)가 흔들린다는 설계 문서의 지적을 지킨다.
"""
from __future__ import annotations


class ServiceAdminCannotApplyError(Exception):
    """서비스 총괄 관리자는 관리사무소 관리자로 신청할 수 없다.

    is_service_admin(is_staff/is_superuser)은 이미 모든 단지에 대해
    can_manage_apartment() 가 무조건 True 를 주는 최상위 권한이다.
    그런 계정이 특정 단지의 MANAGER Membership 을 또 신청/승인받으면
    관리자 명단·승인 큐에 의미 없이 섞여 들어가고, "서비스 운영자가
    관리사무소 관리자 권한을 박탈한다"는 2R-3 설계(동료 관리자는
    서로 못 건드리고 서비스 운영자만 가능)와도 충돌한다 — 그래서
    신청 자체를 접수하지 않는다."""


def apply_for_membership(member, apartment, role, decision_note=""):
    """(member, apartment, role) 조합으로 Membership 신청을 접수한다.

    이미 같은 조합의 신청/소속 이력이 있으면(unique_together 제약과 동일한
    키) 그 행을 그대로 반환하고 새로 만들지 않는다 — 회원가입 화면,
    ApartmentJoinView, ManagerApplyView 세 진입점이 모두 이 함수 하나를
    거쳐야 "이미 신청했는데 또 신청"을 같은 방식으로 안내할 수 있다.

    role 이 MANAGER 이고 member 가 이미 서비스 총괄 관리자면
    ServiceAdminCannotApplyError 를 낸다 — 호출부(회원가입/관리자 신청
    화면)에서 잡아서 안내 메시지로 바꾼다.

    Returns: (membership, created)
    """
    from .models import Membership

    if role == Membership.Role.MANAGER and getattr(member, "is_service_admin", False):
        raise ServiceAdminCannotApplyError(
            "서비스 총괄 관리자는 이미 모든 단지를 관리할 수 있어 "
            "관리사무소 관리자로 별도 신청할 필요가 없습니다."
        )

    existing = Membership.objects.filter(member=member, apartment=apartment, role=role).first()
    if existing:
        return existing, False

    membership = Membership.objects.create(
        member=member, apartment=apartment, role=role,
        status=Membership.Status.REQUESTED, decision_note=decision_note,
    )
    return membership, True


def sync_rule_to_document(rule) -> None:
    """rule.status/effective_* 에 맞춰 연동 Document 를 만들거나 지운다.
    반드시 rag.service.rebuild_index() 까지 호출해야 검색에 반영된다
    (chunking.py 가 apartment_id 를 메타에 넣도록 바뀌었으므로 부분
    재색인이 아니라 항상 전체 재색인이다 — 프로젝트에 증분 색인이
    없다는 기존 한계와 같은 종류).
    """
    from rag.models import Document, SourceType

    from .models import ApartmentRule

    should_publish = rule.status == ApartmentRule.Status.APPROVED and rule.is_within_window()

    if should_publish:
        doc, _created = Document.objects.update_or_create(
            source_key=f"apartment_rule:{rule.pk}",
            defaults=dict(
                title=f"{rule.apartment.name} - {rule.get_category_display()}",
                content_text=f"[{rule.get_category_display()}]\n{rule.content}",
                source_type=SourceType.APARTMENT,
                apartment=rule.apartment,
                region=rule.apartment.region,
                status=Document.Status.APPROVED,
                owner=None,
            ),
        )
        if rule.document_id != doc.pk:
            rule.document = doc
            rule.save(update_fields=["document"])
    else:
        if rule.document_id:
            old_doc_id = rule.document_id
            rule.document = None
            rule.save(update_fields=["document"])
            Document.objects.filter(pk=old_doc_id).delete()

    # 색인은 예약만 한다. 규정 승인은 관리자가 화면에서 누르는 동작이라
    # 여기서 전체 재임베딩을 기다리면 그 요청이 그대로 붙잡힌다.
    from rag import tasks as rag_tasks

    rag_tasks.request_reindex(f"단지 규정 동기화: rule#{rule.pk}")

