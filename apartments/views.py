"""단지 검색 · 가입 · 관리자 신청 · 승인 큐 · 규정 제안/검토 View."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from dashboard.views import AdminRequiredMixin
from members.models import REGION_CHOICES

from . import permissions, scope, services
from .forms import ApartmentJoinForm, ApartmentRuleForm, ApartmentSearchForm, ManagerApplyForm
from .models import Apartment, ApartmentRule, Membership


class ApartmentManagerRequiredMixin(LoginRequiredMixin):
    """apartments 스코프 전용 권한 검사. dashboard.AdminRequiredMixin(is_staff
    기반, 서비스 전역)과는 별개다 — is_staff 로 단지 관리자를 표현하면
    안 된다는 설계 문서의 경고를 지키기 위해 새로 둔다."""

    def dispatch(self, request, *args, **kwargs):
        resp = super().dispatch(request, *args, **kwargs)
        return resp

    def check_apartment_id(self, request, apartment_id):
        if not permissions.can_review_rule(request.user, apartment_id):
            raise Http404("접근 권한이 없습니다.")


class ApartmentSearchView(LoginRequiredMixin, View):
    """지역 → 단지 계단식 검색. 가입·관리자 신청 두 경로의 공용 진입점."""

    def get(self, request):
        form = ApartmentSearchForm(request.GET or {"region": request.user.region})
        apartments = Apartment.objects.none()
        if form.is_valid():
            qs = Apartment.objects.filter(region=form.cleaned_data["region"])
            q = form.cleaned_data.get("q")
            if q:
                qs = qs.filter(name__icontains=q)
            apartments = qs
        return render(
            request, "apartments/apartment_search.html",
            {"form": form, "apartments": apartments},
        )


class ApartmentJoinView(LoginRequiredMixin, View):
    """입주민 가입 신청. design 변경(2R-1) — 코드 자기인증을 없애고
    항상 requested 로 접수한다. 그 단지 관리사무소 관리자(승인된
    Membership 이 아직 없으면 서비스 운영자)가 검토해 승인해야 한다 —
    ManagerApplyView 와 완전히 같은 패턴이고, 승인 시점 처리는
    MembershipDecisionView 에 있다."""

    def get(self, request):
        initial = {}
        apartment_id = request.GET.get("apartment")
        if apartment_id:
            initial["apartment"] = apartment_id
        return render(request, "apartments/join.html", {"form": ApartmentJoinForm(initial=initial)})

    def post(self, request):
        form = ApartmentJoinForm(request.POST)
        if not form.is_valid():
            return render(request, "apartments/join.html", {"form": form}, status=400)

        apartment = form.cleaned_data["apartment"]
        membership, created = services.apply_for_membership(
            request.user, apartment, Membership.Role.RESIDENT,
            form.cleaned_data.get("decision_note", ""),
        )
        if not created:
            messages.info(request, f"이미 신청 이력이 있습니다 (상태: {membership.get_status_display()}).")
        else:
            messages.success(request, f"'{apartment.name}' 입주민 가입 신청이 접수되었습니다. 관리사무소 관리자 승인 후 이용할 수 있습니다.")
        return redirect("apartments:mine")


class ManagerApplyView(LoginRequiredMixin, View):
    """관리사무소 관리자 신청. 항상 requested 로 생성되고 서비스 운영자가
    승인해야 한다 — 첫 관리자든 아니든 승인자가 서비스 운영자 하나뿐이라
    별도 부트스트랩 분기가 필요 없다."""

    def get(self, request):
        return render(request, "apartments/manager_apply.html", {"form": ManagerApplyForm()})

    def post(self, request):
        form = ManagerApplyForm(request.POST)
        if not form.is_valid():
            return render(request, "apartments/manager_apply.html", {"form": form}, status=400)

        apartment = form.cleaned_data["apartment"]
        membership, created = services.apply_for_membership(
            request.user, apartment, Membership.Role.MANAGER,
            form.cleaned_data.get("decision_note", ""),
        )
        if not created:
            messages.info(request, f"이미 신청 이력이 있습니다 (상태: {membership.get_status_display()}).")
        else:
            messages.success(request, "관리사무소 관리자 신청이 접수되었습니다. 서비스 운영자 승인 후 이용할 수 있습니다.")
        return redirect("apartments:mine")


class MyApartmentView(LoginRequiredMixin, View):
    """내 단지 소속 현황 + (관리자/운영자면) 검토 큐 링크."""

    def get(self, request):
        memberships = Membership.objects.filter(member=request.user).select_related("apartment")
        active = scope.current_membership(request)
        managed_ids = permissions.managed_apartment_ids(request.user)
        is_manager_anywhere = bool(managed_ids) and not request.user.is_service_admin
        return render(
            request, "apartments/my_apartment.html",
            {
                "memberships": memberships,
                "active": active,
                "is_manager_anywhere": is_manager_anywhere,
                "pending_membership_count": (
                    Membership.objects.filter(role=Membership.Role.MANAGER, status=Membership.Status.REQUESTED).count()
                    if request.user.is_service_admin else 0
                ),
                # design 변경(2R-1): 입주민 승인도 큐가 생겼으므로 관리자/운영자
                # 모두에게 대기 건수를 보여준다.
                "pending_resident_count": (
                    Membership.objects.filter(
                        apartment_id__in=managed_ids, role=Membership.Role.RESIDENT,
                        status=Membership.Status.REQUESTED,
                    ).count()
                    if managed_ids else 0
                ),
            },
        )


class ApartmentSwitchView(LoginRequiredMixin, View):
    """여러 단지에 소속된 경우(위탁관리 등) 활성 단지를 바꾼다."""

    def post(self, request, pk):
        membership = get_object_or_404(
            Membership, apartment_id=pk, member=request.user, status=Membership.Status.APPROVED,
        )
        scope.set_active_apartment(request, membership.apartment_id)
        return redirect("apartments:mine")


class MembershipQueueView(AdminRequiredMixin, View):
    """관리사무소 관리자 신청 승인 큐. 서비스 운영자 전용 — 관리자 역할은
    쓰기 권한을 새로 여는 것이라 그 단지 관리자 본인이 아니라 서비스
    운영자가 검토해야 한다(자기 단지에 자기를 승인하는 경로를 막는다)."""

    def get(self, request):
        pending = Membership.objects.filter(
            role=Membership.Role.MANAGER, status=Membership.Status.REQUESTED,
        ).select_related("member", "apartment")
        return render(request, "apartments/membership_queue.html", {"pending": pending})


class ResidentApprovalQueueView(LoginRequiredMixin, View):
    """입주민 가입 신청 승인 큐. design 변경(2R-1) — 그 단지 관리사무소
    관리자(또는 서비스 운영자)가 승인한다. 아직 그 단지에 승인된 관리자가
    한 명도 없으면 permissions.managed_apartment_ids() 가 관리자 계정에게
    빈 목록을 돌려주므로, 그 경우엔 서비스 운영자가 대신 처리하면 된다."""

    def get(self, request):
        managed_ids = permissions.managed_apartment_ids(request.user)
        if not managed_ids:
            raise Http404("접근 권한이 없습니다.")
        pending = Membership.objects.filter(
            apartment_id__in=managed_ids, role=Membership.Role.RESIDENT,
            status=Membership.Status.REQUESTED,
        ).select_related("member", "apartment")
        return render(request, "apartments/resident_queue.html", {"pending": pending})


class MembershipDecisionView(LoginRequiredMixin, View):
    """승인/거절. action=approve|reject.

    권한이 역할별로 다르다:
      - MANAGER 신청: 서비스 운영자만 (자기 단지 자기 승인 방지)
      - RESIDENT 신청: 서비스 운영자 또는 그 단지의 승인된 관리자
        (permissions.can_manage_apartment 가 둘 다 판정)
    """

    def post(self, request, pk):
        membership = get_object_or_404(Membership, pk=pk, status=Membership.Status.REQUESTED)

        if membership.role == Membership.Role.MANAGER:
            if not request.user.is_service_admin:
                raise Http404("접근 권한이 없습니다.")
        else:
            if not permissions.can_manage_apartment(request.user, membership.apartment_id):
                raise Http404("접근 권한이 없습니다.")

        action = request.POST.get("action")
        if action not in ("approve", "reject"):
            return HttpResponseForbidden("잘못된 요청입니다.")

        membership.status = Membership.Status.APPROVED if action == "approve" else Membership.Status.REJECTED
        membership.approved_by = request.user
        membership.decided_at = timezone.now()
        membership.decision_note = request.POST.get("decision_note", membership.decision_note)

        if membership.status == Membership.Status.APPROVED and membership.role == Membership.Role.RESIDENT:
            # 승인 시점에만 primary/지역 동기화를 확정한다 — 신청 단계에서는
            # 아직 "진짜 입주민인지" 확인되지 않았으므로 이 부작용을 미룬다.
            has_other = Membership.objects.filter(
                member=membership.member, status=Membership.Status.APPROVED,
            ).exclude(pk=membership.pk).exists()
            membership.is_primary = not has_other
            member = membership.member
            if member.region != membership.apartment.region:
                member.region = membership.apartment.region
                member.save(update_fields=["region"])

        membership.save(update_fields=["status", "approved_by", "decided_at", "decision_note", "is_primary"])
        messages.success(request, f"{membership.member} 님의 신청을 처리했습니다 ({membership.get_status_display()}).")

        redirect_name = "apartments:membership_queue" if membership.role == Membership.Role.MANAGER else "apartments:resident_queue"
        return redirect(redirect_name)


class MembershipLeaveView(LoginRequiredMixin, View):
    """단지 나가기(해지) / 승인 박탈. 본인이 스스로 나가는 것과 관리자가
    남을 내보내는 것을 같은 메커니즘(status=terminated)으로 처리한다 —
    행을 지우지 않으므로 이력은 남고, 원하면 나중에 다시 신청할 수 있다.

    design 변경(2R-3): 역할별로 "누가 내보낼 수 있는가"가 다르다.
      - MANAGER 소속: 본인 또는 서비스 운영자만. 관리사무소 관리자끼리는
        서로 권한을 박탈할 수 없다 — MembershipDecisionView 가 이미
        "MANAGER 승인은 서비스 운영자만" 이라는 것과 대칭이다.
      - RESIDENT 소속: 본인, 그 단지의 승인된 관리자, 또는 서비스 운영자
        (can_manage_apartment 가 셋을 함께 판정).
    """

    def post(self, request, pk):
        membership = get_object_or_404(Membership, pk=pk)
        is_self = membership.member_id == request.user.pk
        if membership.role == Membership.Role.MANAGER:
            allowed = is_self or request.user.is_service_admin
        else:
            allowed = is_self or permissions.can_manage_apartment(request.user, membership.apartment_id)
        if not allowed:
            raise Http404("권한이 없습니다.")

        membership.status = Membership.Status.TERMINATED
        membership.decided_at = timezone.now()
        membership.save(update_fields=["status", "decided_at"])
        if is_self:
            messages.success(request, f"{membership.apartment} 소속이 해지되었습니다.")
        else:
            messages.success(request, f"{membership.member} 님의 {membership.get_role_display()} 승인을 박탈했습니다.")
        return redirect("apartments:mine")


class ResidentRosterView(LoginRequiredMixin, View):
    """승인된 입주민 명단, 단지별로 묶어 본다. 서비스 운영자는 전체 단지,
    관리사무소 관리자는 자기 관리 단지만 — managed_apartment_ids() 가
    이미 그렇게 나눠 준다. 여기서 승인 박탈(MembershipLeaveView 재사용)."""

    def get(self, request):
        managed_ids = permissions.managed_apartment_ids(request.user)
        if not managed_ids:
            raise Http404("접근 권한이 없습니다.")
        residents = Membership.objects.filter(
            apartment_id__in=managed_ids, role=Membership.Role.RESIDENT,
            status=Membership.Status.APPROVED,
        ).select_related("member", "apartment").order_by("apartment__region", "apartment__name")
        return render(request, "apartments/resident_roster.html", {"residents": residents})


class ManagerRosterView(AdminRequiredMixin, View):
    """승인된 관리사무소 관리자 명단(전 단지). 서비스 운영자 전용 —
    권한 박탈은 관리자끼리가 아니라 운영자만 할 수 있어야 한다
    (MembershipLeaveView 참고)."""

    def get(self, request):
        managers = Membership.objects.filter(
            role=Membership.Role.MANAGER, status=Membership.Status.APPROVED,
        ).select_related("member", "apartment").order_by("apartment__region", "apartment__name")
        return render(request, "apartments/manager_roster.html", {"managers": managers})


class ApartmentRuleListView(LoginRequiredMixin, View):
    """현재 컨텍스트 단지의 규정 열람. 승인된 소속이 있어야 볼 수 있다."""

    def get(self, request):
        apartment = scope.current_apartment(request)
        if not apartment and not request.user.is_service_admin:
            messages.info(request, "먼저 단지에 가입해 주세요.")
            return redirect("apartments:search")
        rules = ApartmentRule.objects.filter(
            apartment=apartment, status=ApartmentRule.Status.APPROVED,
        ).select_related("submitted_by") if apartment else ApartmentRule.objects.none()
        return render(
            request, "apartments/rule_list.html",
            {
                "apartment": apartment,
                "rules": rules,
                "can_upload_rule": bool(apartment) and permissions.can_manage_apartment(request.user, apartment.pk),
            },
        )


class ApartmentRuleCreateView(LoginRequiredMixin, View):
    """단지 규정 등록. design 변경(2R-3): "입주민 제안 → 검토대기 →
    관리자 승인" 2단계였던 흐름을 없앴다 — 이제 그 단지를 관리할 수
    있는 사람(관리사무소 관리자/서비스 운영자)만 접근할 수 있고, 등록
    즉시 승인 상태로 반영된다(입주민이 규정을 바꾸고 싶으면 커뮤니티에
    글을 남기는 것으로 대신한다 — apartments/templates/apartments/
    rule_list.html 참고)."""

    def get(self, request):
        apartment = scope.current_apartment(request)
        if not apartment:
            messages.info(request, "먼저 단지에 가입해 주세요.")
            return redirect("apartments:search")
        if not permissions.can_manage_apartment(request.user, apartment.pk):
            raise Http404("접근 권한이 없습니다.")
        return render(request, "apartments/rule_form.html", {"form": ApartmentRuleForm(), "apartment": apartment})

    def post(self, request):
        apartment = scope.current_apartment(request)
        if not apartment:
            raise Http404("단지 소속이 없습니다.")
        if not permissions.can_manage_apartment(request.user, apartment.pk):
            raise Http404("접근 권한이 없습니다.")

        form = ApartmentRuleForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request, "apartments/rule_form.html", {"form": form, "apartment": apartment}, status=400,
            )

        rule = form.save(commit=False)
        rule.apartment = apartment
        rule.submitted_by = request.user
        rule.reviewed_by = request.user
        rule.source_level = ApartmentRule.SourceLevel.OFFICIAL
        rule.status = ApartmentRule.Status.APPROVED
        rule.save()

        try:
            services.sync_rule_to_document(rule)
            messages.success(request, "규정이 등록되어 챗봇 답변에 바로 반영됩니다.")
        except Exception as exc:
            messages.warning(request, f"규정은 저장됐지만 색인 갱신에 실패했습니다: {exc}")
        return redirect("apartments:rule_list")
