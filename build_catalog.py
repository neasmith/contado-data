#!/usr/bin/env python3
"""
Список стран для приложения — catalog.json — из готовых общих файлов стран.

Запуск:  python3 build_catalog.py folder catalog.json
  folder — папка, где лежат <код>-index.json и <код>-comuni.vpkg всех собранных стран

catalog.json:
  {"version": 1, "date": "…",
   "countries": [{"code": "IT", "name": "Italy", "date": "…",
                  "files": ["it-index.json", "it-comuni.vpkg"], "size": байты всех файлов,
                  "municipalities": число коммун,
                  "box": [мин. широта, мин. долгота, макс. широта, макс. долгота] — основная часть страны,
                  "outline": грубый контур страны (~2 км), кольца из [широта, долгота] —
                             по нему приложение понимает, в каких странах есть твои поездки}]}
"""
import glob
import json
import os
import sys
import time

import shapely

CATALOG_TOLERANCE = 0.02           # градусы (~2 км)
MIN_PART_AREA = 0.0005             # кв. градусы (~5 км²): острова меньше в грубый контур не попадают


def coarse_outline(rings):
    """Грубый контур страны: кольца из общего файла → многоугольники → упрощение → снова кольца.
    Дырки (Сан-Марино внутри Италии) сохраняются — приложение считает по правилу чёт-нечет."""
    shapes = []
    for ring in rings:
        if len(ring) >= 4:
            shapes.append(shapely.Polygon([(lon, lat) for lat, lon in ring]))
    result = []
    for polygon in shapes:
        simple = shapely.simplify(polygon, CATALOG_TOLERANCE, preserve_topology=True)
        if simple.is_empty or abs(polygon.area) < MIN_PART_AREA:
            continue
        coords = list(simple.exterior.coords)
        if len(coords) >= 4:
            result.append([[round(y, 3), round(x, 3)] for x, y in coords])
    return result


def main():
    folder, target = sys.argv[1], sys.argv[2]
    countries = []
    for path in sorted(glob.glob(os.path.join(folder, "*-index.json"))):
        with open(path) as f:
            index = json.load(f)
        if index.get("version", 0) < 3:
            continue
        files = [os.path.basename(path), index["comuniFile"]]
        sizes = [os.path.getsize(os.path.join(folder, name)) if os.path.exists(os.path.join(folder, name))
                 else 0 for name in files]
        if 0 in sizes:
            print(f"skip {index['country']}: missing files", flush=True)
            continue
        country = next(a for a in index["areas"] if a["level"] == 2)
        countries.append({
            "code": index["country"], "name": index["name"], "date": index["date"],
            "files": files, "size": sum(sizes),
            "municipalities": sum(1 for a in index["areas"] if a["level"] == 8),
            "box": index["box"],
            "outline": coarse_outline(country.get("outline", [])),
        })
        print(f"{index['country']} {index['name']}: {countries[-1]['municipalities']} municipalities, "
              f"{sum(sizes) / 1e6:.1f} MB", flush=True)
    countries.sort(key=lambda c: c["name"])
    with open(target, "w") as f:
        json.dump({"version": 1, "date": time.strftime("%Y-%m-%d", time.gmtime()), "countries": countries},
                  f, separators=(",", ":"), ensure_ascii=False)
    print(f"{target}: {len(countries)} countries, {os.path.getsize(target) / 1e3:.0f} KB", flush=True)


if __name__ == "__main__":
    main()
