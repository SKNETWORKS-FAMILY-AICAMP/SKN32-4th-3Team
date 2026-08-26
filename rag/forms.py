"""RAG 문서 업로드 · 검색 폼."""
from django import forms

from members.models import REGION_CHOICES

from .models import Document, SourceType

# 3차 admin.py 의 ALLOWED_EXTENSIONS 와 동일하게 유지합니다.
# 4차 추가분: .csv (표 형식 규정·명단 등을 그대로 올릴 수 있도록).
# 추출은 rag/service.py::_read_file() 이 담당한다.
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".csv"}
MAX_UPLOAD_MB = 10


def validate_uploaded_file(uploaded):
    """업로드 파일 1개 검증 (확장자 + 용량).

    4차 추가분: 여러 파일을 한 번에 올릴 수 있게 되면서(DocumentUploadView
    참고) source_file 이 더 이상 ModelForm 필드가 아니라 request.FILES.
    getlist() 로 직접 받는다 — 그래서 파일마다 이 함수를 반복 호출해
    검증한다. ValidationError.messages 에 파일 이름을 포함시켜, 여러 개
    중 어떤 파일이 문제인지 사용자가 바로 알 수 있게 한다.
    """
    from pathlib import Path

    suffix = Path(uploaded.name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise forms.ValidationError(
            f"'{uploaded.name}': 허용되지 않는 형식입니다. "
            f"({', '.join(sorted(ALLOWED_EXTENSIONS))} 만 가능)"
        )
    if uploaded.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise forms.ValidationError(
            f"'{uploaded.name}': 파일은 {MAX_UPLOAD_MB}MB 이하만 올릴 수 있습니다."
        )


class DocumentUploadForm(forms.ModelForm):
    """사용자 문서 업로드.

    3차에는 이 화면이 없었습니다. owner_id 필터와 source_type="manual" 은
    코드에 있었지만 문서를 만들 UI 경로가 없어서 기능이 잠들어 있었습니다.

    업로드된 파일은 source_file 에 보관하고, 추출한 평문을 content_text 에
    넣습니다. content_text 가 색인되는 유일한 필드입니다.

    4차 추가분: source_file 은 더 이상 이 폼의 필드가 아니다 — 여러 파일을
    한 번에 올릴 수 있어야 해서, DocumentUploadView.post() 가 request.
    FILES.getlist("source_file") 로 직접 받아 파일마다 validate_uploaded_
    file() 로 검증하고 Document 를 1개씩 만든다. title 도 필수에서 선택
    으로 바꿨다 — 여러 파일을 올릴 때 제목 1개를 강제하면 오히려 불편
    하고, 비워두면 파일명을 제목으로 쓴다(view 참고).
    """

    title = forms.CharField(
        label="문서 제목 (선택)",
        required=False,
        help_text=(
            "비워두면 파일명이 제목이 됩니다. 파일을 여러 개 올리면 "
            "이 제목 뒤에 파일명이 붙습니다."
        ),
    )

    class Meta:
        model = Document
        fields = ["title", "region"]
        labels = {"region": "적용 지역"}


class RagSearchForm(forms.Form):
    """검색 품질 진단용 폼. 답변 생성 없이 유사 청크만 봅니다."""

    query = forms.CharField(label="질문", max_length=500)
    top_k = forms.IntegerField(label="가져올 청크 수", min_value=1, max_value=20, initial=4)
    region = forms.ChoiceField(
        label="지역",
        choices=[("", "전체")] + list(REGION_CHOICES),
        required=False,
    )
    source_type = forms.ChoiceField(
        label="문서 종류",
        choices=[("", "전체")] + list(SourceType.choices),
        required=False,
    )
