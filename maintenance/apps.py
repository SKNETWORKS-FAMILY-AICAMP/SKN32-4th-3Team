from django.apps import AppConfig


class MaintenanceConfig(AppConfig):
    """운영 유지보수용 앱. **모델이 없어 마이그레이션도 없습니다.**

    앱으로 만든 이유는 시그널을 붙일 자리(`ready()`)가 필요해서입니다.
    프로젝트 전체에 걸치는 정책이라 특정 도메인 앱(rag·boards…) 안에
    두면 다음 사람이 찾지 못합니다.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "maintenance"
    verbose_name = "운영 유지보수"

    def ready(self):
        # 업로드 파일의 수명을 DB 레코드에 맞춥니다(고아 파일 방지).
        # import 를 여기서 하는 이유: 모듈 최상단에서 하면 앱 로딩이
        # 끝나기 전에 모델을 건드리게 됩니다.
        from . import file_cleanup

        file_cleanup.connect()
