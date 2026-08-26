# 배포 런북 — ecobotapt.com

이 서버(공인 IP `<서버 공인 IP>`)에는 이미 Caddy가 `기존-사이트.example.com`을
서빙하고 있습니다. EcoBot은 **같은 Caddy에 사이트 블록 하나를 더 얹는**
방식으로 붙습니다. 포트를 새로 열거나 공유기 설정을 바꿀 필요가 없습니다.

```
인터넷 ──443──▶ Caddy (v2.11.4)
                 ├── 기존-사이트.example.com  ──▶ 127.0.0.1:3000  (Next.js, 기존)
                 └── ecobotapt.com      ──▶ 127.0.0.1:8000  (EcoBot, 신규)
                                                │
                                          gunicorn (systemd: ecobot)
                                                │
                                          Django ──▶ MySQL(127.0.0.1:3306)
                                                 └─▶ FAISS(vector_db/)
```

같은 IP·같은 443 포트에 여러 도메인이 공존하는 것은 **가상 호스팅**입니다.
Caddy는 TLS 핸드셰이크의 SNI와 Host 헤더만 보고 블록을 고르므로, 도메인끼리
소유 관계가 없어도(다른 등록처·다른 TLD여도) 상관없습니다.

---

## 계정 구조 — 먼저 읽으십시오

이 서버는 **두 계정으로 나뉘어** 있고, 그 경계가 배포 명령에 직접 영향을 줍니다.

| 계정 | uid | sudo | 역할 |
|---|---|---|---|
| `앱 계정` | 1001 | ❌ | 코드·venv·`.env` 소유. 앱이 이 권한으로 돕니다 |
| `관리 계정` | 1000 | ✅ | 시스템 변경 담당 (apt·systemd·Caddy) |

**`관리 계정` 는 `<앱계정 홈>` 에 들어갈 수 없습니다.** 권한이 `drwxr-x---`
(0750, `앱계정:앱계정`)이라 `cd` 조차 되지 않습니다. 그래서 이런 명령은
**실패합니다**:

```bash
# 관리 계정 세션에서 — Permission denied
sudo tee -a /etc/caddy/Caddyfile < $PROJECT_DIR/deploy/Caddyfile.ecobotapt
```

`sudo` 를 붙였는데도 실패하는 이유는, **리다이렉트(`<`)를 sudo 가 아니라
셸이 수행**하기 때문입니다. 파일을 여는 주체는 여전히 `관리 계정` 입니다.
`sudo cp` 처럼 **명령 자체가 root 로 도는** 형태만 통합니다.

### 그래서 이렇게 나눕니다

**`관리 계정` 세션에서** (실행 위치 무관 — 전부 절대 경로입니다):

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh deps
sudo bash $PROJECT_DIR/deploy/install-system.sh service
sudo bash $PROJECT_DIR/deploy/install-system.sh caddy
```

스크립트 전체가 root 로 실행되므로 위 문제가 없습니다. **몇 번 실행해도
안전**하고(중복 추가하지 않습니다), `caddy` 단계는 DNS 를 먼저 확인하고
설정이 유효하지 않으면 백업본으로 되돌립니다.

**`앱 계정` 세션에서** (sudo 불필요): 나머지 전부 — git, venv, pip,
`.env` 작성, `migrate`, `collectstatic`, `seed_*`.

> `su - 관리 계정` 로 전환하든 별도로 SSH 접속하든 상관없습니다.
> 홈 디렉터리 위치도 무관합니다.

### 순서

```
앱 계정      : git checkout, venv 생성, .env 작성
관리 계정   : install-system.sh deps      ← apt
앱 계정      : pip install (mysqlclient 포함)
관리 계정   : install-system.sh db        ← MySQL DB·계정
앱 계정      : migrate, collectstatic, createsuperuser, seed_*, rag_reindex
관리 계정   : install-system.sh service   ← systemd
관리 계정   : install-system.sh caddy     ← Caddy (DNS 확인 후)
```

**`deps` 가 `pip install` 보다 먼저여야 합니다** — `mysqlclient` 가 그 헤더를
필요로 합니다. 그리고 `mysqlclient` 가 없으면 `migrate` 뿐 아니라
**`collectstatic` 까지 실패합니다.** Django 가 관리 명령 실행 전에 시스템
체크를 돌리면서 DB 백엔드를 임포트하기 때문입니다:

```
django.core.exceptions.ImproperlyConfigured: Error loading MySQLdb module.
```

`db` 는 `.env` 를 읽어 계정을 만들므로 `.env` 작성 후여야 하고,
`service` 는 venv·`.env` 가 준비된 뒤여야 합니다(스크립트가 확인하고 없으면
멈춥니다).

---

## 0. 사전 준비

### 0-1. DNS — ✅ 완료됨 (2026-08-26 확인)

```
ecobotapt.com      → <서버 공인 IP>
www.ecobotapt.com  → <서버 공인 IP>
```

권위 네임서버 조회도 일치하고, **원본 IP가 그대로 반환되므로 회색 구름
(DNS only)도 확인**됐습니다(주황 구름이면 Cloudflare IP 가 나옵니다).
아래는 다시 설정해야 할 때를 위한 기록입니다.

| Type | Name | Content | Proxy status |
|---|---|---|---|
| A | `@` | `<서버 공인 IP>` | **DNS only (회색 구름)** |
| A | `www` | `<서버 공인 IP>` | **DNS only (회색 구름)** |

> ⚠️ **반드시 회색 구름으로.** 주황 구름(프록시)을 켜면 Caddy의 Let's Encrypt
> HTTP-01 검증이 Cloudflare에 가로막혀 인증서 발급이 실패합니다. 기존
> `기존-사이트.example.com`도 DNS only로 되어 있으니 동일하게 맞추십시오.

전파 확인 — **아래가 IP를 뱉기 전에는 8단계(Caddy)로 넘어가지 마십시오.**

```bash
dig +short ecobotapt.com      # → <서버 공인 IP>
dig +short www.ecobotapt.com  # → <서버 공인 IP>
```

### 0-2. OpenAI 배포용 키

챗봇은 로그인이 필요하지만 **회원가입은 누구나 가능**하고, 코드에 rate limit이
없습니다. 공개되는 순간 이 키가 사실상 외부에 열린 것과 같습니다.

1. 배포 **전용** 키를 새로 발급 (개발 키를 재사용하지 마십시오)
2. OpenAI 대시보드에서 **월 사용 한도(hard limit)** 를 먼저 설정

---

## 1. 시스템 패키지  — `관리 계정` 세션

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh deps
```

스크립트가 하는 일은 이것뿐입니다:

```bash
sudo apt update
sudo apt install -y default-libmysqlclient-dev
```

**이건 uv를 써도 우회할 수 없습니다.** `mysqlclient`는 C 확장이고, PyPI에
**Windows 휠(`win_amd64`)만** 올라와 있습니다(2.2.8 기준). Linux·macOS에서는
소스 빌드가 강제되고, MySQL 클라이언트 헤더가 없으면 이렇게 실패합니다:

```
Exception: Can not find valid pkg-config name.
```

`build-essential`과 `pkg-config`는 이 서버에 이미 있습니다.

> **팀원 환경별로 정리하면:**
> - **Windows** — 휠이 있어 `pip install`만으로 끝납니다. 빌드 도구 불필요.
> - **Linux** — `sudo apt install default-libmysqlclient-dev`
> - **macOS** — `brew install mysql-client pkg-config` + `PKG_CONFIG_PATH` 설정
>
> 개발용으로 MySQL이 필요 없다면 `DB_ENGINE=sqlite3`을 쓰면 `mysqlclient`
> 자체가 필요 없습니다(퀵스타트 경로). 서버에서만 MySQL을 쓰면 됩니다.

---

## 2. 코드 · 가상환경

### 방법 A — uv (권장)

이 서버에는 uv가 이미 있습니다(`~/.local/bin/uv`).

```bash
cd $PROJECT_DIR
git checkout deploy/ecobotapt          # 또는 병합된 main

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-prod.txt
```

uv는 **`python3.12-venv` apt 패키지가 필요 없습니다.** 표준 `python3 -m venv`는
`ensurepip`(= 그 apt 패키지)에 의존하는데, uv는 자체 방식으로 venv를 만들고
필요하면 해당 버전의 Python을 직접 내려받기까지 합니다. 설치도 훨씬 빠릅니다.

### 방법 B — 표준 venv

> apt 패키지가 하나 더 필요해 계정을 두 번 오가야 합니다.
> **방법 A(uv)를 쓰면 이 왕복이 없습니다.**

```bash
# ↓ 관리 계정 세션에서 (이 서버에 아직 없습니다)
sudo apt install -y python3.12-venv

# ↓ 다시 앱 계정 세션에서
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-prod.txt
```

> 이 패키지 없이 `python3 -m venv`를 돌리면 **pip이 빠진 반쪽 venv**가 만들어지고,
> 에러는 마지막 줄에만 나와서 놓치기 쉽습니다. `.venv/bin/pip --version`으로
> 확인하십시오.

`systemd` 유닛(`deploy/ecobot.service`)은 어느 방법으로 만들었든
`.venv/bin/gunicorn`을 실행하므로 그대로 동작합니다.

`requirements-prod.txt`는 `requirements.txt`에서 **sentence-transformers와
langchain 계열을 뺀** 것입니다. 전자는 torch(2~3GB)를 끌고 옵니다. 두 묶음 모두
함수 안에서 지연 임포트되므로(`rag/embeddings.py:49`, `rag/service.py:153`)
없어도 앱은 정상 기동합니다 — 단 아래 조건에서만 필요합니다.

| .env 값 | 필요한 패키지 |
|---|---|
| `EMBEDDING_BACKEND=local` | `sentence-transformers` |
| `RAG_PIPELINE=langchain` | `langchain-*` |

운영 기본값은 `openai` / `legacy`입니다. **설치 결과 venv는 238MB**(uv) / 307MB(pip)입니다.

---

## 3. 환경변수

```bash
cp .env.production.example .env
python3 -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

출력된 키를 `.env`의 `DJANGO_SECRET_KEY`에 붙여넣고, `DB_PASSWORD`와
`OPENAI_API_KEY`를 채우십시오. `.env`는 `.gitignore`에 있습니다 —
**절대 커밋하지 마십시오.**

핵심 값:

```env
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=ecobotapt.com,www.ecobotapt.com,127.0.0.1
DJANGO_BEHIND_PROXY=True      # Caddy의 X-Forwarded-Proto를 신뢰
DJANGO_HSTS_SECONDS=0         # HTTPS 확인 후 31536000으로
```

`CSRF_TRUSTED_ORIGINS`는 비워두면 `ALLOWED_HOSTS`에서 자동으로 유도됩니다
(`https://ecobotapt.com`, `https://www.ecobotapt.com`). 도메인을 추가할 때
두 곳을 고치다 한 곳을 빠뜨리는 사고를 막기 위한 설계입니다.

---

## 4. 데이터베이스  — `관리 계정` 세션

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh db
```

MySQL 은 이미 이 서버에서 돌고 있지만, **root 가 `auth_socket` 방식이라 OS
root 로만 접속됩니다.** `앱 계정` 로는 `mysql -u root` 가 이렇게 거부됩니다:

```
ERROR 1698 (28000): Access denied for user 'root'@'localhost'
```

스크립트는 **`.env` 에서 `DB_NAME`/`DB_USER`/`DB_PASSWORD` 를 읽어** 아래를
실행합니다. 비밀번호를 두 곳에 적지 않기 위해서입니다 — 어긋나면 증상이
"Django 만 접속 실패"라 원인에서 멉니다. 만든 계정으로 실제 접속까지
확인하고 끝냅니다.

```sql
CREATE DATABASE IF NOT EXISTS ecora CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'app_user'@'localhost' IDENTIFIED BY '<.env 값>';
ALTER USER 'app_user'@'localhost' IDENTIFIED BY '<.env 값>';
GRANT ALL PRIVILEGES ON ecora.* TO 'app_user'@'localhost';
FLUSH PRIVILEGES;
```

> ⚠️ **MySQL이 현재 `*:3306`으로 모든 인터페이스에 열려 있습니다.**
> 공유기가 3306을 포워딩하지 않으면 인터넷에서는 안 닿지만, LAN의 다른
> 기기에서는 접속 가능합니다. 이참에 조이는 것을 권합니다:
> **`관리 계정` 세션에서** `/etc/mysql/mysql.conf.d/mysqld.cnf` 에
> `bind-address = 127.0.0.1` 을 넣고 `sudo systemctl restart mysql`.
> (Django 는 `DB_HOST=127.0.0.1` 이라 영향 없음)

---

## 5. 초기화

`manage.py` 명령은 `.env`를 자동으로 읽습니다 — `config/settings.py:20`이
`load_dotenv()`로 직접 로딩하므로 `source .env` 같은 단계가 필요 없습니다.

```bash
cd $PROJECT_DIR

.venv/bin/python manage.py makemigrations boards
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py seed_docs
.venv/bin/python manage.py rag_reindex
.venv/bin/python manage.py seed_apartments
```

`rag_reindex`는 OpenAI 임베딩 API를 호출하므로 문서 수에 따라 수 분 걸리고
비용이 발생합니다.

> `collectstatic`을 빠뜨리면 **배포 후 모든 페이지가 500**입니다.
> 운영에서는 `CompressedManifestStaticFilesStorage`를 쓰는데, 이 저장소는
> 템플릿의 `{% static %}`이 가리키는 파일이 manifest에 없으면 예외를 던집니다.

---

## 6. gunicorn (systemd)  — `관리 계정` 세션

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh service
```

유닛 설치 → `media/`·`vector_db/` 준비 → `enable --now` 까지 합니다.
venv 나 `.env` 가 없으면 기동하지 않고 멈춥니다.

유닛은 `User=앱 계정` 로 돌고 경로가 전부 절대 경로이므로, **누가 설치하든
서비스는 `앱 계정` 권한으로 실행됩니다.** 파일 소유권을 바꿀 필요가 없습니다.

```bash
systemctl status ecobot
journalctl -u ecobot -n 40 --no-pager
```

설정은 `deploy/gunicorn.conf.py`에 있습니다. 두 값이 중요합니다.

- `worker_class = "gthread"`, `workers=3`, `threads=4` — 이 앱의 요청은 CPU가
  아니라 **대기**(OpenAI 호출)가 대부분입니다. 이 서버는 8코어지만
  Next.js·Ollama·MySQL이 함께 도므로 3개로 잡았습니다.
- `timeout = 180` — 기본값 30초로는 부족합니다. 문서를 업로드하면 **그 요청
  안에서** `rebuild_index()`가 전체 문서를 다시 임베딩합니다
  (`rag/views.py:167`). 30초를 넘기면 워커가 죽어 업로드가 500으로 끝나고
  인덱스는 중간 상태로 남습니다.

---

## 7. 로컬 확인 (Caddy 붙이기 전)

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/ \
     -H "Host: ecobotapt.com" -H "X-Forwarded-Proto: https"     # → 200
```

200이 아니면 `journalctl -u ecobot -n 50`을 먼저 보십시오. 여기서 안 되는 걸
Caddy에 붙이면 원인 지점이 두 배로 늘어납니다.

---

## 8. Caddy  — `관리 계정` 세션

**0-1의 DNS 전파가 끝난 것을 확인한 뒤에** 진행하십시오.

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh caddy
```

스크립트가 순서대로 처리합니다: DNS 확인 → 타임스탬프 백업 → 중복 확인 후
블록 추가 → `caddy validate` → **유효하지 않으면 백업본으로 롤백** →
`systemctl reload caddy`.

> ⚠️ `sudo tee -a ... < deploy/...` 형태는 쓰지 마십시오. 리다이렉트를 셸이
> 수행해서 `관리 계정` 권한으로 읽으려다 Permission denied 가 납니다.
> (위 **계정 구조** 절 참고)

인증서는 첫 요청 때 Caddy가 Let's Encrypt에서 자동 발급합니다.

```bash
journalctl -u caddy -f    # "certificate obtained successfully" 확인
```

> ⚠️ DNS가 없는 상태로 reload하면 검증이 실패하고, 반복하면 Let's Encrypt의
> 실패 한도(호스트당 시간당 5회)에 걸려 몇 시간 막힙니다. 기존 위키에는
> 영향이 없지만 새 도메인만 그동안 못 씁니다.

---

## 9. 최종 확인

```bash
curl -sI https://ecobotapt.com | head -3
curl -sI https://www.ecobotapt.com | head -3      # → 301 → ecobotapt.com
```

> 이 서버 **안에서** 자기 공인 IP로 접속하면 NAT 헤어핀 때문에 타임아웃이
> 납니다(정상). **휴대폰 LTE로 Wi-Fi를 끄고** 열어보는 것이 확실합니다.

브라우저에서 확인할 것:

- [ ] 자물쇠 아이콘 (인증서 정상)
- [ ] CSS·이미지가 깨지지 않음 (collectstatic + WhiteNoise)
- [ ] **로그인이 됨** — 여기서 403이 나면 CSRF 설정 문제입니다
- [ ] 챗봇 질문에 답변이 옴 (OpenAI 키)
- [ ] 프로필 사진 업로드 후 화면에 보임 (media 서빙)
- [ ] 기존 https://기존-사이트.example.com 이 그대로 동작

전부 통과했으면 HSTS를 켜십시오 — `.env`에 `DJANGO_HSTS_SECONDS=31536000`
후 `sudo systemctl reload ecobot`.
**되돌리기 어렵습니다.** 브라우저가 그 기간 동안 이 도메인을 HTTPS로만
접속하므로, 인증서가 정상인 것을 확인한 뒤에 켜십시오.

---

## 10. 운영

### 코드 갱신

```bash
cd $PROJECT_DIR
git pull
.venv/bin/pip install -r requirements-prod.txt      # 의존성 바뀐 경우만
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload ecobot                         # 무중단 워커 교체
```

### 되돌리기 — 마이그레이션을 적용했으면 코드만 되돌릴 수 없습니다

`migrate` 를 한 번이라도 돌렸으면 **코드를 옛 커밋으로 되돌려도 DB 는 그대로**
입니다. 옛 코드는 새 스키마를 모르고, 마이그레이션 이력도 앞서 있는 상태로
남습니다. 되돌리려면 코드와 DB 를 **함께** 되돌려야 하고, 그러려면 되돌리는
마이그레이션이 준비돼 있어야 합니다.

`premerge-backup` 태그(2026-08-26 forksync 병합 직전)가 그런 경우입니다.
제약을 태그 자체에 적어 두었으니 쓰기 전에 읽으십시오:

```bash
git tag -n99 premerge-backup     # ⚠ 롤백 지점이 아니라 "비교용" 이라고 적혀 있습니다
```

**그래서 되돌릴 일이 생길 것 같으면 `migrate` 전에 덤프를 뜹니다** (10절 백업).
코드 태그 하나로는 되돌아가지지 않습니다.

### 로그

```bash
journalctl -u ecobot -f      # 앱 (gunicorn 액세스 로그 + Django 예외)
journalctl -u caddy -f       # TLS · 프록시
```

Django 기본 로깅은 `DEBUG=False`이면 예외를 `ADMINS`에게 메일로만 보냅니다.
`ADMINS`를 설정하지 않았으므로 그대로 두면 **500 에러가 아무 데도 안 남습니다**.
`config/settings.py`의 `LOGGING`이 이를 stdout으로 돌려 journald가 받게 합니다.

### 백업

재생성 불가능한 것은 둘뿐입니다.

```bash
mysqldump -u app_user -p ecora | gzip > ~/backup/ecora-$(date +%F).sql.gz
tar czf ~/backup/media-$(date +%F).tar.gz media/
```

`vector_db/`는 `rag_reindex`로 다시 만들 수 있으므로 백업 대상이 아닙니다
(다만 재생성에 OpenAI 비용이 듭니다).

### 알아둘 것: 동적 IP

집 회선이라 공인 IP가 바뀌면 **`ecobotapt.com`과 `기존-사이트.example.com`이 함께
죽습니다.** Cloudflare API 토큰으로 DDNS 스크립트를 걸어두면 IP 변경 시 A
레코드가 자동으로 따라갑니다. 도메인이 둘로 늘었으니 영향 범위도 늘었습니다.

---

## 11. 배포 후 보안·운영

배포가 끝난 뒤에 붙이는 것들입니다. 순서는 상관없습니다.

### 11-1. MySQL 을 로컬로 제한  — `관리 계정` 세션

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh mysql-secure
```

Ubuntu 기본 설정은 `/etc/mysql/mysql.conf.d/mysqld.cnf` 의 `bind-address` 가
**주석 처리돼 있어** MySQL 이 모든 인터페이스(`*:3306`)로 열립니다. 공유기가
3306 을 포워딩하지 않으면 인터넷에서는 안 닿지만, **LAN 의 다른 기기에서는
접속할 수 있습니다.**

스크립트는 이렇게 진행합니다:

1. 설정 백업 → `bind-address = 127.0.0.1` 적용
2. **로컬이 아닌 연결이 있는지 확인** — 있으면 잠그는 순간 끊기므로 중단
3. `systemctl restart mysql`
4. 실제로 `127.0.0.1:3306` 으로만 열렸는지 확인

`ecobot` 은 재시작하지 않아도 됩니다. Django 의 `CONN_MAX_AGE` 기본값이 0
이라 요청마다 연결을 새로 맺기 때문입니다.

> `mysqlx-bind-address` 는 이미 `127.0.0.1` 로 설정돼 있어 33060 포트는
> 처음부터 로컬 전용이었습니다.

### 11-2. Cloudflare DDNS  — 공인 IP 변경 추적

**이 서버는 가정 회선이라 공인 IP 가 고정이 아닙니다.** IP 가 바뀌면 A 레코드가
옛 주소를 가리킨 채 남고, **`ecobotapt.com` 과 `기존-사이트.example.com` 이 함께
죽습니다.** 도메인이 둘로 늘면서 영향 범위도 늘었습니다.

**① 토큰 만들기** (사람이 해야 합니다)

Cloudflare 대시보드 → 프로필 → **API Tokens** → Create Token
→ **Edit zone DNS** 템플릿

| 항목 | 값 |
|---|---|
| Permissions | Zone / DNS / **Edit** |
| Zone Resources | Include / Specific zone / `ecobotapt.com` |
| Zone Resources (추가) | Include / Specific zone / `example.com` |

**두 zone 을 모두 넣어야 위키까지 따라갑니다.** 하나만 넣으면 그 도메인만
갱신되고 나머지는 조용히 실패합니다.

**② 설치** — `관리 계정` 세션

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh ddns
sudo nano /etc/ddns-cloudflare/config.env       # CF_API_TOKEN= 에 토큰 붙여넣기
sudo $PROJECT_DIR/deploy/ddns-cloudflare.sh   # 수동으로 한 번
sudo systemctl enable --now ddns-cloudflare.timer
```

토큰을 먼저 넣고 `ddns` 를 실행하면 스크립트가 수동 실행·타이머 활성화까지
알아서 합니다. **타이머를 켜기 전에 반드시 한 번 직접 돌려 보십시오** —
토큰 권한이 모자라면 여기서 드러납니다. 타이머로만 돌리면 5 분 뒤 저널을
봐야 압니다.

**동작**

- 5 분 주기 + 부팅 1 분 뒤 (`ddns-cloudflare.timer`)
- 공인 IP 를 3 곳에서 순서대로 조회하고 **형식 검증을 통과한 값만** 씁니다.
  조회처가 죽었을 때 빈 값으로 레코드를 덮어쓰면 도메인이 통째로 날아갑니다.
- 현재 값과 같으면 API 를 호출하지 않습니다.
- 갱신할 때 **`proxied` 와 `ttl` 을 원래 값 그대로** 실어 보냅니다. 빠뜨리면
  Cloudflare 가 기본값으로 되돌려서, 회색 구름이던 레코드가 주황 구름이 되고
  **Caddy 의 인증서 갱신이 막힙니다.**
- 시작할 때 토큰을 먼저 검증합니다. 안 하면 토큰이 틀렸을 때 모든 조회가 빈
  결과로 돌아와 "zone 을 찾지 못했습니다"로 보이고, 원인에서 먼 곳(Cloudflare
  zone 설정)을 뒤지게 됩니다.

```bash
journalctl -u ddns-cloudflare -f          # 로그
systemctl list-timers ddns-cloudflare     # 다음 실행 시각
```

**남는 한계:** IP 가 바뀐 뒤 최대 5 분 + DNS TTL(자동 = 300 초)만큼은 옛 주소를
가리킵니다. 주기를 줄여도 TTL 이 하한이라 실익이 적습니다.

### 11-3. 재색인 워커 (백그라운드 색인)  — `관리 계정` 세션

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh reindex
```

**무엇이 바뀌는가**

예전에는 문서를 올리거나 지울 때 **그 요청 안에서** 전체 문서를 다시
임베딩했습니다. 문서가 늘면 수십 초가 걸리는데, gunicorn 타임아웃을 넘기면
워커가 죽어 **업로드가 500 으로 끝나고 색인은 중간 상태로 남습니다** —
문서는 이미 저장됐는데도.

```
웹 요청 → dirty=True + 트리거 파일 touch → 즉시 반환 (실측 16ms)
                     │
            systemd path 유닛이 감지
                     ▼
     manage.py rag_reindex --if-needed   (ecobot-reindex.service)
```

유닛이 셋입니다:

| 유닛 | 역할 |
|---|---|
| `ecobot-reindex.service` | 실제 색인 (oneshot) |
| `ecobot-reindex.path` | 트리거 파일이 바뀌면 즉시 깨움 |
| `ecobot-reindex.timer` | 5분 주기 안전망 — 트리거를 놓쳤거나 실패해 재시도할 때 |

둘이 겹쳐 깨워도 명령 안의 **flock** 이 중복 실행을 막습니다. DB 플래그로
락을 흉내 내지 않은 이유는, 프로세스가 죽으면 `running` 이 영구히 남아
이후 실행이 전부 막히기 때문입니다. flock 은 프로세스가 사라지면 커널이
알아서 풀어 줍니다.

**실패해도 잃지 않습니다** — `dirty` 는 성공했을 때만 내려갑니다. 실패하면
남아 있어 다음 타이머가 다시 시도합니다.

**삭제한 문서가 재색인 전까지 인용되지 않는가?** — 안 됩니다.
`search()` 가 DB 에 없거나 승인 상태가 아닌 `document_id` 를 결과에서
걸러냅니다(`rag/service.py:_drop_missing_documents`). 재색인을 비동기로
바꾸면서 생긴 창을 막는 장치입니다.

```bash
journalctl -u ecobot-reindex -f          # 색인 로그
systemctl list-timers ecobot-reindex     # 다음 안전망 실행
curl -s https://ecobotapt.com/rag/status/ | jq   # 진행 상태 (로그인 필요)
```

> ⚠️ 이 기능은 마이그레이션이 필요합니다. `앱 계정` 계정에서:
> `cd $PROJECT_DIR && .venv/bin/python manage.py migrate`

### 11-4. 챗봇 하루 한도

`.env` 의 `CHAT_DAILY_LIMIT`(기본 50)으로 사용자당 하루 질문 수를
제한합니다. `0` 이면 무제한이고, superuser 는 기본 면제입니다
(`CHAT_DAILY_LIMIT_EXEMPT_STAFF`).

한도를 넘기면 **HTTP 429** 와 함께 언제 초기화되는지 알려 줍니다. 차단된
질문은 대화 기록에 남지 않습니다 — 답변 없는 질문만 쌓이면 사용자가
혼란스러워집니다.

**왜 캐시가 아니라 DB 로 세는가:** gunicorn 워커가 3개인데 Django 기본
캐시(locmem)는 프로세스마다 따로입니다. 캐시로 세면 실효 한도가 3배가 되고,
워커 수를 바꿀 때마다 조용히 달라집니다. `ChatLog` 는 이미 질문마다 한 행씩
쌓이고 `created_at` 에 인덱스가 있어 그걸 세는 편이 정확합니다.

> OpenAI 대시보드의 월 한도는 **"터지기 직전에 서비스 전체를 멈추는"
> 차단기**입니다. 그것만 두면 한 명이 예산을 다 쓰고 나머지 사용자가 전부
> 막힙니다. 둘 다 두십시오.

### 11-5. 고아 업로드 파일 정리

**문제.** Django 의 `FileField` 는 레코드를 지워도 **파일을 지우지 않습니다.**
1.3 에서 의도적으로 바뀐 동작인데(트랜잭션 롤백 시 파일을 되살릴 수 없어서),
그대로 두면 `media/` 에 고아 파일이 계속 쌓입니다. 파일을 교체 업로드할 때
밀려난 옛 파일도 마찬가지입니다.

**앞으로 생기는 것** — `maintenance` 앱이 시그널로 막습니다. 별도 설치가
필요 없고, 프로젝트 안의 `FileField` 를 자동으로 찾아 붙습니다:

```
members.Member.photo
boards.Board.attachment
rag.Document.source_file
apartments.ApartmentRule.source_file / .photo
```

삭제는 `transaction.on_commit()` 이후에만 일어납니다. 롤백된 삭제 때문에
파일을 날리면 되돌릴 방법이 없습니다.

**이미 쌓인 것** — 관리 명령으로 회수합니다. **기본은 조회만** 합니다:

```bash
cd $PROJECT_DIR
.venv/bin/python manage.py cleanup_orphan_files                  # 목록만
.venv/bin/python manage.py cleanup_orphan_files --delete         # 실제 삭제
.venv/bin/python manage.py cleanup_orphan_files --delete --min-age-hours 1
```

`--min-age-hours`(기본 24)는 **업로드 중인 파일을 지우지 않기 위한 장치**
입니다. 업로드는 "파일을 먼저 쓰고 → 레코드를 저장"하는 순서라, 그 사이에
명령이 돌면 방금 올라온 파일이 고아로 보입니다.

**주간 자동 정리 (선택)** — `관리 계정` 세션:

```bash
sudo bash $PROJECT_DIR/deploy/install-system.sh cleanup
```

매주 일요일 04:00 에 7일 이상 된 고아 파일을 지웁니다. 설치 스크립트가
**켜기 전에 무엇이 지워질지 먼저 보여 줍니다.**

> ⚠️ 자동 삭제입니다. 시그널이 제 역할을 하면 지울 것이 없으므로 이건
> 안전망일 뿐입니다 — 굳이 켜지 않아도 됩니다. 필요할 때 위 명령을 손으로
> 돌리는 편이 안전합니다.

---

## 12. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| 로그인·글쓰기가 **403 CSRF verification failed** | `DJANGO_BEHIND_PROXY=False`거나 `CSRF_TRUSTED_ORIGINS`에 도메인 누락 | `.env` 확인 → `reload ecobot` |
| 모든 페이지 **500** | `collectstatic` 미실행 (manifest에 파일 없음) | `collectstatic --noinput` |
| CSS·JS만 **404** | WhiteNoise 미들웨어 위치가 잘못됨 | `SecurityMiddleware` **바로 다음**이어야 함 |
| **400 Bad Request** | `ALLOWED_HOSTS`에 그 도메인 없음 | `.env`의 `DJANGO_ALLOWED_HOSTS` |
| 업로드 파일만 404 | `DJANGO_SERVE_MEDIA=False`인데 Caddy `file_server` 미설정 | 둘 중 하나로 통일 |
| 인증서 발급 실패 | DNS 미전파 / Cloudflare 프록시 켜짐 | `dig` 확인, 회색 구름으로 |
| 문서 업로드가 **500**, 로그에 `WORKER TIMEOUT` | 재색인이 타임아웃 초과 | `gunicorn.conf.py`의 `timeout` ↑ (Caddy도 함께) |
| `pip install mysqlclient` 컴파일 에러 | `default-libmysqlclient-dev` 없음 | 1단계 apt |
| `ModuleNotFoundError: sentence_transformers` | `.env`가 `EMBEDDING_BACKEND=local` | `openai`로 되돌리거나 패키지 설치 |
| 리다이렉트 무한루프 | `DJANGO_SSL_REDIRECT=True` + 프록시 헤더 불일치 | `False`로 (Caddy가 이미 처리) |
| DDNS 가 조용히 안 돎 | 토큰 Zone Resources 에 그 도메인 누락 | `journalctl -u ddns-cloudflare` 확인 |
| 인증서 갱신이 갑자기 실패 | DDNS 가 `proxied` 를 되돌려 주황 구름이 됨 | Cloudflare 에서 회색 구름으로 |
| MySQL 접속 거부(다른 기기) | `mysql-secure` 로 로컬 제한됨 | 의도된 동작 |
| 업로드해도 검색에 안 잡힘 | 재색인이 아직 안 돌았거나 실패 | `journalctl -u ecobot-reindex -n 30` |
| 재색인이 계속 실패 | OpenAI 키·한도 문제 | `/rag/status/` 의 `last_error` 확인 |
| 챗봇이 **429** 를 반환 | 하루 한도 초과 | 의도된 동작. `CHAT_DAILY_LIMIT` 조정 |
| 챗봇이 **503** 을 반환 | 임베딩 API 실패(지출 한도·키) | `journalctl -u ecobot` 에 원인이 남습니다 |
| `media/` 용량이 계속 증가 | 고아 파일 누적 | `cleanup_orphan_files` (11-5) |

### 롤백  — `관리 계정` 세션

`install-system.sh caddy` 는 실행할 때마다 타임스탬프 백업을 남깁니다.

```bash
ls -t /etc/caddy/Caddyfile.bak.*        # 가장 최근 것을 고릅니다
sudo cp /etc/caddy/Caddyfile.bak.<타임스탬프> /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy             # ecobotapt.com만 내려가고 위키는 유지
sudo systemctl stop ecobot
```

`validate` 가 롤백에도 있는 이유는, 잘못된 백업본을 되돌린 채 reload 하면
**위키까지 함께 죽기** 때문입니다. 한 파일에 두 사이트가 들어 있습니다.
