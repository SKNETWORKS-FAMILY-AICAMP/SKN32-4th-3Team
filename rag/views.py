"""RAG 문서 조회 · 업로드 · 인덱스 관리 View.

실행 로직(업로드 · 재빌드 · 진단 검색 · 삭제)은 구현 완료 상태이고,
화면 렌더링(목록 · 상세)은 프론트 이식 단계에서 채웁니다.
"""
import json
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from dashboard.views import AdminRequiredMixin

from . import service
from .forms import DocumentUploadForm
from .models import Document, SourceType


def _json(payload, status=200):
    return JsonResponse(
        payload, status=status, safe=False, json_dumps_params={"ensure_ascii": False}
    )


class DocumentListView(LoginRequiredMixin, View):
    """문서 목록 화면 — "내가 올린 문서 + 공용 문서".

    3차 GET /api/admin/documents 는 관리자 전용이었지만, 업로드 기능이
    생기면 일반 사용자도 자기 문서를 봐야 합니다. 관리자용 청크 통계는
    dashboard:documents 로 분리했습니다.
    """

    def get(self, request):
        from django.db.models import Q

        docs = Document.objects.filter(
            Q(owner=request.user) | Q(source_type__in=[SourceType.LAW, SourceType.GUIDE])
        ).order_by("source_type", "title")
        return render(request, "rag/document_list.html", {"documents": docs})


class DocumentUploadView(LoginRequiredMixin, View):
    """사용자 문서 업로드 + 색인.

    ⚠️ 3차 POST /api/admin/upload 의 버그를 고친 자리입니다.

    3차 동작:  파일을 data/guide/ 폴더에 저장 → rebuild_index()
    문제:      rebuild_index() 는 RAG_SOURCE=db 일 때 documents 테이블을
               읽습니다. 방금 올린 파일은 폴더에만 있으므로 **색인되지
               않습니다.** 응답의 indexed_chunks 는 기존 문서의 청크
               수라서 성공처럼 보이고, seed_docs 를 다시 돌릴 때까지
               조용히 무시됩니다.
    4차 동작:  ① source_file 저장 → ② 평문 추출해 content_text 에 저장
               (Document 레코드 생성) → ③ rebuild_index() — 테이블을
               읽으니 반드시 포함됩니다.

    평문 추출은 service._read_file() 을 재사용합니다 (pdf/txt/md +
    cp949 폴백). 추출 결과가 비어 있으면(스캔 PDF 등) 색인할 수 없으므로
    레코드를 만들지 않고 에러를 돌려줍니다.
    """

    def get(self, request):
        return render(request, "rag/document_upload.html", {"form": DocumentUploadForm()})

    def post(self, request):
        form = DocumentUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request, "rag/document_upload.html", {"form": form}, status=400
            )

        doc: Document = form.save(commit=False)
        # 관리자가 대시보드에서 as_public 로 올리면 전체 공개 가이드가 된다.
        # (3차 admin 업로드가 guide 폴더에 저장하던 의미를 계승.
        #  일반 회원의 as_public 요청은 무시 — 권한 상승 방지)
        if request.POST.get("as_public") and request.user.is_service_admin:
            doc.owner = None
            doc.source_type = SourceType.GUIDE
        else:
            doc.owner = request.user
            doc.source_type = SourceType.MANUAL
        doc.save()  # 파일이 MEDIA_ROOT 에 저장되고 path 가 생긴다

        text = service._read_file(Path(doc.source_file.path)).strip()
        if not text:
            doc.delete()  # 레코드와 파일을 함께 정리
            form.add_error(
                "source_file",
                "텍스트를 추출하지 못했습니다. 스캔 PDF 라면 OCR 처리 후 다시 올려 주세요.",
            )
            return render(
                request, "rag/document_upload.html", {"form": form}, status=400
            )

        doc.content_text = text
        doc.source_key = f"upload:{doc.pk}"
        doc.save(update_fields=["content_text", "source_key"])

        # 업로드 즉시 검색에 잡히도록 색인을 재구축한다.
        try:
            result = service.rebuild_index()
            messages.success(
                request,
                f"'{doc.title}' 업로드 완료 — 문서 {result['documents']}개가 색인되었습니다.",
            )
        except Exception as exc:
            # 문서는 저장됐지만 색인 실패. 3차처럼 부분 성공을 알린다.
            messages.warning(
                request,
                f"'{doc.title}' 은 저장됐지만 색인에 실패했습니다: {exc} — "
                "관리자 대시보드에서 재색인을 실행해 주세요.",
            )
        return redirect("rag:documents")


class DocumentDetailView(LoginRequiredMixin, View):
    """근거 원문 보기. 답변의 출처 링크가 여기로 옵니다.

    권한: 공용 문서(law·guide)는 누구나, 사용자 업로드(manual)는 owner
    본인만. 검사를 빠뜨리면 pk 를 바꿔가며 남의 문서를 읽을 수 있습니다.

    ?file=1 이면 업로드 원본 파일을 그대로 내려줍니다 (PDF 대조용).
    """

    def get(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if not doc.is_public and doc.owner_id != request.user.pk:
            # 존재 여부도 숨기기 위해 403 이 아니라 404 를 씁니다.
            raise Http404("문서를 찾을 수 없습니다.")

        if request.GET.get("file") and doc.source_file:
            return FileResponse(doc.source_file.open("rb"))

        return render(request, "rag/document_detail.html", {"document": doc})


class DocumentDeleteView(LoginRequiredMixin, View):
    """문서 삭제 + 색인 재구축.

    삭제 후 rebuild_index() 를 부르지 않으면 지운 문서가 계속 검색됩니다
    (3차 트러블슈팅 4번 "삭제한 파일의 옛 레코드가 잘못 인용됨"과 같은
    계열의 문제).

    권한: owner 본인 또는 서비스 관리자. 공용 문서(law·guide)는 화면이
    아니라 seed_docs 가 폴더 기준으로 관리하므로 여기서 못 지웁니다.
    """

    def post(self, request, pk):
        doc = get_object_or_404(Document, pk=pk)
        if doc.is_public:
            messages.error(request, "공용 문서는 관리 명령(seed_docs)으로만 삭제할 수 있습니다.")
            return redirect("rag:documents")
        if doc.owner_id != request.user.pk and not request.user.is_service_admin:
            raise Http404("문서를 찾을 수 없습니다.")

        title = doc.title
        doc.delete()
        try:
            service.rebuild_index()
            messages.success(request, f"'{title}' 을(를) 삭제하고 색인을 갱신했습니다.")
        except Exception as exc:
            messages.warning(request, f"'{title}' 은 삭제됐지만 색인 갱신에 실패했습니다: {exc}")
        return redirect("rag:documents")


class IndexStatusView(LoginRequiredMixin, View):
    """인덱스 존재 여부. 3차 GET /api/rag/status 와 응답 형식이 같습니다."""

    def get(self, request):
        from . import vector_store

        return _json({"index_exists": vector_store.index_exists()})


class IndexRebuildView(AdminRequiredMixin, View):
    """인덱스 전체 재구축. 3차 POST /api/rag/rebuild 대응.

    임베딩 API 를 문서 전체에 대해 호출하므로 비용이 발생합니다.
    embeddings.py 의 디스크 캐시가 같은 텍스트 재호출을 막아주지만,
    관리자 전용으로 둡니다. 오류 응답 형식({"detail": ...})은 3차
    HTTPException 과 맞춰서 프론트 에러 처리를 재사용합니다.
    """

    def post(self, request):
        try:
            return _json(service.rebuild_index())
        except Exception as exc:
            return _json({"detail": f"인덱싱에 실패했습니다: {exc}"}, status=500)


class RagSearchView(AdminRequiredMixin, View):
    """검색 품질 진단용. 답변 생성 없이 유사 청크만 반환합니다.

    3차 POST /api/rag/search 대응 — balanced=False 로 순수 유사도 순
    결과를 봅니다. _apply_quota() 를 조정할 때 이 화면으로 확인합니다.
    응답 형식 {"count", "results"} 는 3차와 동일합니다.

    본문은 JSON({"query": ..., "top_k": ...}) 과 폼 인코딩을 모두
    받습니다 — 3차 프론트는 JSON 을 보냈고, Django 템플릿 폼은 폼
    인코딩을 보내기 때문입니다.
    """

    def post(self, request):
        if request.content_type == "application/json":
            try:
                body = json.loads(request.body or b"{}")
            except ValueError:
                return _json({"detail": "잘못된 JSON 본문입니다."}, status=400)
        else:
            body = request.POST

        query = (body.get("query") or "").strip()
        if not query:
            return _json({"detail": "query 가 필요합니다."}, status=400)
        try:
            top_k = max(1, min(int(body.get("top_k", 4)), 20))
        except (TypeError, ValueError):
            top_k = 4

        try:
            results = service.search(
                query, top_k, owner_id=request.user.pk, balanced=False
            )
            return _json({"count": len(results), "results": results})
        except Exception as exc:
            return _json({"detail": f"검색에 실패했습니다: {exc}"}, status=500)
