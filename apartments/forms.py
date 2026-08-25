"""단지 검색 · 가입 · 신청 · 규정 제안 폼."""
from django import forms

from members.models import REGION_CHOICES

from .models import Apartment, ApartmentRule


class ApartmentSearchForm(forms.Form):
    """지역을 먼저 고르고 그 지역 단지만 검색한다(계단식 선택)."""

    region = forms.ChoiceField(label="지역", choices=REGION_CHOICES)
    q = forms.CharField(label="단지명", required=False)


class ApartmentJoinForm(forms.Form):
    """입주민 가입 신청. design 변경(2R-1) — 코드 자기인증을 없애고,
    신청 후 그 단지 관리사무소 관리자(또는 서비스 운영자)의 승인을
    거쳐야 승인된다(ManagerApplyForm 과 같은 패턴)."""

    apartment = forms.ModelChoiceField(label="단지", queryset=Apartment.objects.all())
    decision_note = forms.CharField(
        label="동/호수 등 확인 메모(선택)", widget=forms.Textarea, required=False,
    )


class ManagerApplyForm(forms.Form):
    """관리사무소 관리자 신청. 서비스 운영자가 사람이 검토하므로
    코드 인증 대신 근거 메모를 받는다."""

    apartment = forms.ModelChoiceField(label="단지", queryset=Apartment.objects.all())
    decision_note = forms.CharField(
        label="위탁관리 근거(계약서 번호 등)", widget=forms.Textarea, required=False,
    )


class ApartmentRuleForm(forms.ModelForm):
    """단지 규정 등록 폼. design 변경(2R-3): 이제 관리사무소 관리자만
    이 폼을 쓸 수 있으므로 source_level 은 뷰에서 항상 official 로
    고정한다 — 사용자가 고를 필요가 없어 필드에서 뺐다."""

    class Meta:
        model = ApartmentRule
        fields = ["category", "content", "photo", "effective_from", "effective_until"]
        labels = {
            "category": "분류",
            "content": "규정 내용",
            "photo": "배출장소 사진",
            "effective_from": "적용 시작일",
            "effective_until": "적용 종료일",
        }
        widgets = {
            "content": forms.Textarea(attrs={"rows": 4}),
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "effective_until": forms.DateInput(attrs={"type": "date"}),
        }
