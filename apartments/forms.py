"""단지 검색 · 가입 · 신청 · 규정 제안 폼."""
from django import forms

from members.models import REGION_CHOICES

from .models import Apartment, ApartmentRule


class ApartmentOfficeForm(forms.ModelForm):
    """관리사무소 연락처 등록/수정. 관리사무소 관리자(또는 서비스
    운영자)만 접근하는 화면이므로 권한 체크는 뷰(ApartmentOfficeInfoView)
    에서 apartments.permissions.can_manage_apartment 로 한다."""

    class Meta:
        model = Apartment
        fields = ["address", "office_phone", "office_hours"]
        labels = {"address": "주소", "office_phone": "전화번호", "office_hours": "운영시간"}


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


# rag/forms.py:DocumentUploadForm 의 ALLOWED_EXTENSIONS/MAX_UPLOAD_MB 와
# 값을 맞춘 로컬 사본이다. apartments 는 rag 를 import 하지 않는 방향으로
# 의존성을 유지한다(rag/models.py Document 모델 docstring 참고) — 그래서
# rag.forms 를 가져다 쓰지 않고 여기서 따로 정의한다.
RULE_FILE_ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
RULE_FILE_MAX_MB = 10


class ApartmentRuleForm(forms.ModelForm):
    """단지 규정 등록 폼. design 변경(2R-3): 이제 관리사무소 관리자만
    이 폼을 쓸 수 있으므로 source_level 은 뷰에서 항상 official 로
    고정한다 — 사용자가 고를 필요가 없어 필드에서 뺐다.

    design 변경(4차 추가): 규정을 직접 타이핑하는 대신 PDF/txt/md 파일을
    올리는 경로도 지원한다. content 를 폼에서는 필수가 아니게 풀어두고,
    content 또는 source_file 둘 중 하나만 있으면 통과시킨다 — 실제 평문
    추출(파일 → content)은 뷰(ApartmentRuleCreateView)가 rule 저장 후
    source_file.path 를 읽어 처리한다(save() 로 파일이 디스크에 먼저
    있어야 경로를 얻을 수 있으므로 폼 단계에서는 하지 않는다)."""

    class Meta:
        model = ApartmentRule
        fields = ["category", "content", "source_file", "photo", "effective_from", "effective_until"]
        labels = {
            "category": "분류",
            "content": "규정 내용",
            "source_file": "규정 원문 파일(선택)",
            "photo": "배출장소 사진",
            "effective_from": "적용 시작일",
            "effective_until": "적용 종료일",
        }
        help_texts = {
            "content": "직접 입력하거나, 아래에 파일을 올리면 자동으로 채워집니다.",
            "source_file": f"{', '.join(sorted(RULE_FILE_ALLOWED_EXTENSIONS))} 파일만 가능 (최대 {RULE_FILE_MAX_MB}MB)",
        }
        widgets = {
            "content": forms.Textarea(attrs={"rows": 4}),
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "effective_until": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].required = False

    def clean_source_file(self):
        uploaded = self.cleaned_data.get("source_file")
        if not uploaded:
            return uploaded

        from pathlib import Path

        suffix = Path(uploaded.name).suffix.lower()
        if suffix not in RULE_FILE_ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"허용되지 않는 형식입니다. ({', '.join(sorted(RULE_FILE_ALLOWED_EXTENSIONS))} 만 가능)"
            )
        if uploaded.size > RULE_FILE_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f"파일은 {RULE_FILE_MAX_MB}MB 이하만 올릴 수 있습니다.")
        return uploaded

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("content") and not cleaned.get("source_file"):
            raise forms.ValidationError("규정 내용을 직접 입력하거나, 파일을 올려 주세요.")
        return cleaned
