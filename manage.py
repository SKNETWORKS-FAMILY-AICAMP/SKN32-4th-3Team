#!/usr/bin/env python
"""Django 관리 명령 진입점."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django 를 불러올 수 없습니다. 가상환경이 활성화되어 있는지, "
            "pip install -r requirements.txt 를 실행했는지 확인하십시오."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
