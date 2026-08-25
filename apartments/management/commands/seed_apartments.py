"""데모용 단지 1~2개 + 데모 규정 1건을 만든다.

이번 라운드는 K-apt 연동 없이 인프라만 갖추기로 확정했으므로, 실사용
데이터 대신 기능을 시연할 수 있는 최소 데이터만 수동으로 심는다.

    python manage.py seed_apartments
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apartments.models import Apartment, ApartmentRule
from apartments.services import sync_rule_to_document

DEMO_APARTMENTS = [
    {
        "name": "래미안 서울숲",
        "region": "seoul",
        "address": "서울특별시 성동구",
    },
    {
        "name": "천안 센트럴파크",
        "region": "cheonan",
        "address": "충청남도 천안시",
    },
]

DEMO_RULE = {
    "category": ApartmentRule.Category.TIME,
    "content": "음식물쓰레기는 매주 수요일·토요일 저녁 7시~9시에만 배출 가능합니다. "
               "그 외 요일에는 배출하지 마시고, 부득이한 경우 관리사무소에 문의해 주세요.",
    "source_level": ApartmentRule.SourceLevel.OFFICIAL,
    "status": ApartmentRule.Status.APPROVED,
}


class Command(BaseCommand):
    help = "데모용 아파트 단지와 승인된 규정 1건을 시드합니다."

    def handle(self, *args, **options):
        created_apartments = []
        for data in DEMO_APARTMENTS:
            apt, created = Apartment.objects.update_or_create(
                name=data["name"], region=data["region"],
                defaults={"address": data["address"]},
            )
            created_apartments.append(apt)
            verb = "생성" if created else "갱신"
            self.stdout.write(f"[단지 {verb}] {apt}")

        first = created_apartments[0]
        rule, rule_created = ApartmentRule.objects.get_or_create(
            apartment=first, category=DEMO_RULE["category"], content=DEMO_RULE["content"],
            defaults={
                "source_level": DEMO_RULE["source_level"],
                "status": DEMO_RULE["status"],
                # submitted_by 는 NOT NULL 이라 시스템 계정 없이는 채울 수 없다.
                # 데모 규정은 첫 서비스 운영자(superuser) 계정으로 등록한다.
                "submitted_by": self._first_admin(),
            },
        )
        if rule_created or rule.status != ApartmentRule.Status.APPROVED:
            rule.status = ApartmentRule.Status.APPROVED
            rule.save(update_fields=["status"])
            sync_rule_to_document(rule)
            self.stdout.write(f"[규정 승인 동기화] {rule} → rag.Document 생성 + 재색인 완료")
        else:
            self.stdout.write(f"[규정 이미 존재] {rule}")

    def _first_admin(self):
        from members.models import Member

        admin = Member.objects.filter(is_superuser=True).order_by("pk").first()
        if not admin:
            raise SystemExit(
                "서비스 운영자 계정이 없습니다. "
                "python manage.py createsuperuser 로 먼저 계정을 만들어 주세요."
            )
        return admin
