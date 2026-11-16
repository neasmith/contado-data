#!/usr/bin/env python3
"""
Что собирать в этот раз — для GitHub Actions (строка matrix=… в $GITHUB_OUTPUT).
Страны одной выгрузки Geofabrik собираются вместе: выгрузка качается один раз.

  COUNTRIES="IT FR"  — только эти страны (пусто — все из countries.json)
  PROBE="europe/greece europe/hungary" — вместо сборки только показать уровни границ этих выгрузок
"""
import json
import os

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "countries.json")) as f:
    config = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

probe = os.environ.get("PROBE", "").split()
wanted = [c.upper() for c in os.environ.get("COUNTRIES", "").replace(",", " ").split()]

if probe:
    jobs = [{"source": source, "codes": ""} for source in probe]
else:
    sources = {}
    for code, item in config.items():
        if not wanted or code in wanted:
            sources.setdefault(item["source"], []).append(code)
    jobs = [{"source": source, "codes": " ".join(codes)} for source, codes in sorted(sources.items())]

print("matrix=" + json.dumps(jobs))
