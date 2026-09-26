"""يحوّل استعلامات SQLAlchemy text() في وحدة Python إلى PREPARE statements لـ psql.

الاستخدام (من مجلد backend):  python -m scripts.gen_prepared_sql app.dashboard.queries [NAME1,NAME2] > /tmp/dash_prepared.sql
الناتج: `PREPARE <name_lower> AS ...` مع استبدال :param بـ $n (نفس الاسم => نفس الرقم)،
و`-- params:` قبل كل استعلام بترتيب المعاملات. اختبارات SQL تستدعيها بـ EXECUTE =>
تُختبر نصوص الاستعلامات الحقيقية كما يرسلها التطبيق (وليس نسخاً منها).
"""
from __future__ import annotations

import importlib
import re
import sys

PARAM = re.compile(r"(?<![:\w]):([a-zA-Z_][a-zA-Z0-9_]*)")


def convert(sql: str) -> tuple[str, list[str]]:
    names: list[str] = []

    def sub(m: re.Match[str]) -> str:
        if m.group(1) not in names:
            names.append(m.group(1))
        return f"${names.index(m.group(1)) + 1}"

    # إزالة التعليقات أولاً حتى لا تُحسب كلمات مثل ':x' داخلها
    body = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return PARAM.sub(sub, body).strip().rstrip(";"), names


def main(module_name: str, only: set[str] | None = None) -> None:
    mod = importlib.import_module(module_name)
    for name, value in vars(mod).items():
        if not name.isupper() or name.startswith("_") or (only and name not in only):
            continue
        sql = getattr(value, "text", value)
        if not isinstance(sql, str):
            continue
        body, params = convert(sql)
        print(f"-- {name} params: {params}\nPREPARE {name.lower()} AS {body};\n")


if __name__ == "__main__":
    main(sys.argv[1], set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None)
