#!/usr/bin/env python3
"""
Что собирать в этот раз — для GitHub Actions (строки matrix=… и codes=… в $GITHUB_OUTPUT).

Страны — все, что есть в списке выгрузок Geofabrik (index-v1-nogeom.json): у каждой выгрузки там
коды стран ISO. Для страны берём выгрузку только с ней одной, а если такой нет — самую маленькую
из общих (Сенегал и Гамбия). countries.json может указать свою (source): например, если страна
лежит в двух выгрузках или её вообще нет в списке (Сан-Марино внутри Италии).
Страны одной выгрузки собираются вместе: выгрузка качается один раз.

  COUNTRIES="IT FR" — только эти страны; "ALL" — все; "NEW RU" — все, которых ещё нет, и RU.
  Пусто (сборка по расписанию) — страны, которых ещё нет в выпуске, и шестая часть остальных
    по очереди: каждая страна обновляется раз в полгода (границы меняются редко,
    а весь мир с Geofabrik — около 80 ГБ).
  PROBE="europe/greece" — вместо сборки только показать уровни границ этих выгрузок.
  ASSETS — файлы, которые уже лежат в выпуске (по одному в строке).
"""
import json
import os
import sys
import time

BASE = "https://download.geofabrik.de/"
ROTATION = 6                         # за сколько месяцев обновляются все страны
MAX_JOBS = 250                       # у GitHub не больше 256 задач в одной сборке


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def load_config():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "countries.json")) as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def geofabrik_sources():
    """Код страны → выгрузка («europe/italy»). Список выгрузок сборка уже скачала (curl с повторами:
    Geofabrik иногда не пускает с первого раза) — в файл geofabrik-index.json."""
    with open("geofabrik-index.json") as f:
        index = json.load(f)
    single, shared = {}, {}
    for feature in index["features"]:
        props = feature["properties"]
        codes = [c.upper() for c in props.get("iso3166-1:alpha2", [])]
        url = props.get("urls", {}).get("pbf", "")
        if not codes or not url.startswith(BASE) or not url.endswith("-latest.osm.pbf"):
            continue
        path = url[len(BASE):-len("-latest.osm.pbf")]
        for code in codes:
            if len(codes) == 1:
                # Если выгрузок одной страны несколько, берём самую общую (короче путь):
                # «europe/france», а не «europe/france/corse».
                if code not in single or path.count("/") < single[code].count("/"):
                    single[code] = path
            elif code not in shared or len(codes) < shared[code][1]:
                shared[code] = (path, len(codes))
    sources = {code: path for code, (path, _) in shared.items()}
    sources.update(single)
    return sources


def main():
    probe = os.environ.get("PROBE", "").split()
    if probe:
        print("matrix=" + json.dumps([{"source": s, "codes": ""} for s in probe]))
        print("codes=")
        return

    config = load_config()
    sources = geofabrik_sources()
    for code, item in config.items():
        if "source" in item:
            sources[code] = " ".join(item["source"]) if isinstance(item["source"], list) else item["source"]
    everything = sorted(sources)
    log(f"{len(everything)} countries")

    wanted = os.environ.get("COUNTRIES", "").replace(",", " ").upper().split()
    present = {line.split("-")[0].upper() for line in os.environ.get("ASSETS", "").split()
               if line.endswith("-index.json")}
    if wanted == ["ALL"]:
        chosen = everything
    elif "NEW" in wanted:
        # Все страны, которых ещё нет в выпуске, и те, что перечислены рядом с NEW.
        extra = [c for c in wanted if c != "NEW" and c in sources]
        chosen = sorted({c for c in everything if c not in present} | set(extra))
    elif wanted:
        unknown = [c for c in wanted if c not in sources]
        if unknown:
            log(f"not in Geofabrik or countries.json: {' '.join(unknown)}")
        chosen = [c for c in wanted if c in sources]
    else:
        month = time.gmtime().tm_mon
        chosen = [c for i, c in enumerate(everything) if c not in present or i % ROTATION == month % ROTATION]

    groups = {}
    for code in chosen:
        groups.setdefault(sources[code], []).append(code)
    jobs = [{"source": source, "codes": " ".join(codes)} for source, codes in sorted(groups.items())]
    if len(jobs) > MAX_JOBS:
        log(f"{len(jobs)} jobs — only the first {MAX_JOBS} this time, the rest next run")
        jobs = jobs[:MAX_JOBS]
    for job in jobs:
        log(f"  {job['source']}: {job['codes']}")
    print("matrix=" + json.dumps(jobs))
    print("codes=" + " ".join(everything))


if __name__ == "__main__":
    main()
