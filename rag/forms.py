"""RAG 문서 업로드 · 검색 폼."""
from django import forms

from members.models import REGION_CHOICES

from .models import Document, SourceType

# 3차 admin.py 의 ALLOWED_EXTENSIONS 와 동일하게 유지합니다.
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_UPLOAD_MB = 10


class DocumentUploadForm(forms.ModelForm):
    """사용자 문서 업로드.

    3차에는 이 화면이 없었습니다. owner_id 필터와 source_type="manual" 은
    코드에 있었지만 문서를 만들 UI 경로가 없어서 기능이 잠들어 있었습니다.

    업로드된 파일은 source_file 에 보관하고, 추출한 평문을 content_text 에
    넣습니다. content_text 가 색인되는 유일한 필드입니다.
    """

    class Meta:
        model = Document
        fields = ["title", "source_file", "region"]
        labels = {
            "title": "문서 제목",
            "source_file": "파일 (.txt / .md / .pdf)",
            "region": "적용 지역",
        }
        help_texts = {
            "title": "답변 출처에 이 제목이 표시됩니다. 지역이 드러나는 제목이 좋습니다.",
        }

    def clean_source_file(self):
        uploaded = self.cleaned_data.get("source_file")
        if not uploaded:
            raise forms.ValidationError("파일을 선택해 주세요.")

        from pathlib import Path

        suffix = Path(uploaded.name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"허용되지 않는 형식입니다. ({', '.join(sorted(ALLOWED_EXTENSIONS))} 만 가능)"
            )
        if uploaded.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"파일은 {MAX_UPLOAD_MB}MB 이하만 올릴 수 있습니다.")
        return uploaded


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
