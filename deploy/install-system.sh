#!/usr/bin/env bash
#
# EcoBot 시스템 설치 — root 권한이 필요한 작업만 모았습니다.
#
# 이 저장소는 앱 계정 소유이고 <앱계정 홈> 는 0750 입니다. sudo 를 가진
# 계정(관리 계정)은 이 디렉터리를 **통과조차 못 합니다**. 그래서
#
#     sudo tee -a /etc/caddy/Caddyfile < deploy/Caddyfile.ecobotapt
#
# 같은 형태는 실패합니다 — 리다이렉트(<)는 sudo 가 아니라 **셸**이 수행하므로
# 관리 계정 권한으로 파일을 열려다 Permission denied 가 납니다.
#
# 이 스크립트는 전체가 root 로 실행되므로 그 문제가 없습니다.
# 실행 위치는 상관없습니다(모든 경로가 절대 경로입니다).
#
#   sudo bash $PROJECT_DIR/deploy/install-system.sh deps
#   sudo bash $PROJECT_DIR/deploy/install-system.sh service
#   sudo bash $PROJECT_DIR/deploy/install-system.sh caddy
#
# 런북(docs/deploy.md)의 순서대로 deps → (사람이 .env·DB 준비) →
# service → caddy 로 나눠 두었습니다. `all` 은 셋을 연달아 실행합니다.
#
# 몇 번을 실행해도 안전합니다(중복 추가하지 않습니다).

set -euo pipefail

PROJECT_DIR="$PROJECT_DIR"
APP_USER="앱 계정"
CADDYFILE="/etc/caddy/Caddyfile"
DOMAIN="ecobotapt.com"
SERVER_IP="<서버 공인 IP>"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<USAGE
사용법:  sudo bash $0 <단계>

  deps     시스템 패키지 (default-libmysqlclient-dev)
  db       MySQL 데이터베이스·계정 생성  ← .env 작성 후에
  service  systemd 유닛 설치 + 기동      ← migrate 후에
  caddy    Caddy 사이트 블록 추가        ← 마지막
  all      위 넷을 연달아

런북: $PROJECT_DIR/docs/deploy.md
USAGE
}

# 사용법을 root 검사보다 먼저 봅니다 — 인자를 몰라서 실행해 본 사람에게
# "root 로 실행하십시오"만 띄우면 무엇을 하라는 건지 알 수 없습니다.
case "${1:-}" in
    deps|db|service|caddy|all) ;;
    *) usage; exit 1 ;;
esac

[[ $EUID -eq 0 ]] || die "root 로 실행해야 합니다:  sudo bash $0 $1"
[[ -d $PROJECT_DIR ]] || die "$PROJECT_DIR 가 없습니다"


install_deps() {
    say "1) 시스템 패키지"
    # mysqlclient 는 C 확장이고 PyPI 에 Windows 휠만 있습니다.
    # Linux 에서는 소스 빌드가 강제되므로 MySQL 클라이언트 헤더가 필요합니다.
    # (uv 를 써도 이건 우회되지 않습니다)
    apt-get update -qq
    apt-get install -y default-libmysqlclient-dev
    ok "default-libmysqlclient-dev 설치됨"

    if pkg-config --exists mysqlclient; then
        ok "pkg-config 가 mysqlclient 를 인식합니다 — pip 설치가 가능합니다"
    else
        warn "pkg-config 가 아직 mysqlclient 를 못 찾습니다. 아래를 확인하십시오:"
        warn "  pkg-config --list-all | grep -i mysql"
    fi

    cat <<MSG

  다음은 앱 계정 계정에서 (sudo 불필요):
      cd $PROJECT_DIR
      uv pip install --python .venv/bin/python -r requirements-prod.txt
MSG
}


install_db() {
    say "2) MySQL 데이터베이스 · 계정"
    local envfile="$PROJECT_DIR/.env"
    [[ -f $envfile ]] || die "$envfile 가 없습니다. 앱 계정 계정에서 먼저 작성하십시오"

    # 비밀번호를 여기에 적지 않고 .env 에서 읽습니다. 두 곳에 적으면 반드시
    # 어긋나고, 그때 증상이 "Django 만 접속 실패"라 원인에서 멉니다.
    # root 로 실행 중이므로 0600 인 .env 도 읽을 수 있습니다.
    local name user pass
    name=$(grep -E "^DB_NAME=" "$envfile" | cut -d= -f2- | tr -d "\r")
    user=$(grep -E "^DB_USER=" "$envfile" | cut -d= -f2- | tr -d "\r")
    pass=$(grep -E "^DB_PASSWORD=" "$envfile" | cut -d= -f2- | tr -d "\r")

    [[ -n $name && -n $user && -n $pass ]] \
        || die ".env 의 DB_NAME / DB_USER / DB_PASSWORD 를 확인하십시오"
    [[ $pass != *"넣으십시오"* ]] \
        || die ".env 의 DB_PASSWORD 가 아직 예시값입니다"
    [[ $pass != *"'"* ]] \
        || die "DB_PASSWORD 에 작은따옴표가 있으면 이 스크립트로 처리할 수 없습니다"

    # MySQL root 는 auth_socket 이라 OS root 로만 붙습니다(그래서 이 단계가
    # 앱 계정 계정에서 불가능합니다). 아래는 몇 번 돌려도 안전합니다.
    mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`$name\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$user'@'localhost' IDENTIFIED BY '$pass';
ALTER USER '$user'@'localhost' IDENTIFIED BY '$pass';
GRANT ALL PRIVILEGES ON \`$name\`.* TO '$user'@'localhost';
FLUSH PRIVILEGES;
SQL
    ok "데이터베이스 '$name' · 계정 '$user'@'localhost' 준비됨"

    # 실제로 붙는지 확인합니다 — 여기서 확인해 두면 migrate 가 실패할 때
    # 원인이 DB 인지 Django 설정인지 나눠서 볼 수 있습니다.
    if mysql -u "$user" -p"$pass" -e "USE \`$name\`; SELECT 1;" >/dev/null 2>&1; then
        ok "'$user' 로 '$name' 접속 확인됨"
    else
        die "계정은 만들었으나 접속에 실패했습니다"
    fi

    cat <<MSG

  다음은 앱 계정 계정에서 (sudo 불필요):
      cd $PROJECT_DIR
      .venv/bin/python manage.py migrate
      .venv/bin/python manage.py createsuperuser
      .venv/bin/python manage.py seed_docs
      .venv/bin/python manage.py rag_reindex
      .venv/bin/python manage.py seed_apartments
MSG
}


install_service() {
    say "3) systemd 유닛"
    local src="$PROJECT_DIR/deploy/ecobot.service"
    [[ -f $src ]] || die "$src 가 없습니다"

    install -m 644 -o root -g root "$src" /etc/systemd/system/ecobot.service
    systemctl daemon-reload
    ok "/etc/systemd/system/ecobot.service 설치됨"

    # 유닛은 User=앱 계정 로 돌고 경로가 전부 절대 경로입니다. 누가 설치하든
    # 서비스는 앱 계정 권한으로 실행되므로 파일 소유권을 바꿀 필요가 없습니다.
    [[ -x "$PROJECT_DIR/.venv/bin/gunicorn" ]] \
        || die ".venv/bin/gunicorn 이 없습니다. 앱 계정 계정에서 의존성을 먼저 설치하십시오"
    [[ -f "$PROJECT_DIR/.env" ]] \
        || die ".env 가 없습니다. 런북 3단계를 먼저 끝내십시오"
    ok "gunicorn · .env 확인됨"

    # 서비스가 써야 하는 디렉터리(유닛의 ReadWritePaths 와 일치해야 합니다)
    for d in media vector_db; do
        install -d -o "$APP_USER" -g "$APP_USER" "$PROJECT_DIR/$d"
    done
    ok "media/ · vector_db/ 준비됨"

    systemctl enable --now ecobot
    sleep 2
    if systemctl is-active --quiet ecobot; then
        ok "ecobot 기동됨"
    else
        warn "기동 실패 — 로그:  journalctl -u ecobot -n 40 --no-pager"
        exit 1
    fi
}


install_caddy() {
    say "4) Caddy 사이트 블록"
    local snippet="$PROJECT_DIR/deploy/Caddyfile.ecobotapt"
    [[ -f $snippet ]] || die "$snippet 가 없습니다"

    # DNS 가 먼저여야 합니다. 없는 상태로 reload 하면 Let's Encrypt 검증이
    # 실패하고, 반복하면 발급 한도에 걸려 몇 시간 막힙니다.
    local resolved
    resolved=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
    if [[ $resolved != "$SERVER_IP" ]]; then
        die "DNS 미확인: $DOMAIN → '${resolved:-없음}' (기대: $SERVER_IP)"
    fi
    ok "DNS 확인됨 ($DOMAIN → $SERVER_IP)"

    if grep -q "$DOMAIN" "$CADDYFILE"; then
        warn "$CADDYFILE 에 $DOMAIN 블록이 이미 있습니다 — 추가하지 않습니다"
    else
        local backup="${CADDYFILE}.bak.$(date +%Y%m%d-%H%M%S)"
        cp -a "$CADDYFILE" "$backup"
        ok "백업: $backup"

        cat "$snippet" >> "$CADDYFILE"
        ok "블록 추가됨"

        if ! caddy validate --config "$CADDYFILE" --adapter caddyfile 2>&1 | grep -q "Valid configuration"; then
            cp -a "$backup" "$CADDYFILE"
            die "설정이 유효하지 않아 되돌렸습니다. 위 오류를 확인하십시오"
        fi
        ok "caddy validate 통과"
    fi

    systemctl reload caddy
    ok "caddy reload 됨 (무중단 — 기존-사이트.example.com 은 끊기지 않습니다)"

    cat <<MSG

  인증서 발급을 확인하십시오 (첫 요청 때 자동 발급):
      journalctl -u caddy -f
      → "certificate obtained successfully" 를 기다리십시오

  그 다음 휴대폰에서 Wi-Fi 를 끄고 https://$DOMAIN 을 열어 보십시오.
  (서버 안에서 자기 공인 IP 로 접속하면 NAT 헤어핀 때문에 타임아웃이 납니다)
MSG
}


case "$1" in
    deps)    install_deps ;;
    db)      install_db ;;
    service) install_service ;;
    caddy)   install_caddy ;;
    all)     install_deps; install_db; install_service; install_caddy ;;
esac

say "완료"
