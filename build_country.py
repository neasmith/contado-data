#!/usr/bin/env python3
"""
Готовит границы стран для приложения Contado из выгрузки OpenStreetMap (.osm.pbf).

Запуск:
  python3 build_country.py boundaries.osm.pbf out IT [SM …]
      собрать страны. Из одной выгрузки можно собрать несколько стран: из Италии — Италию и Сан-Марино.
      Какой уровень границ у регионов, провинций и коммун, скрипт определяет сам (auto_config):
      регионы — уровень с кодами ISO3166-2 «<код>-…», коммуны и провинции — уровни, которые покрывают
      почти всю страну. countries.json — только исключения, где это не так, и названия уровней.
  python3 build_country.py --probe boundaries.osm.pbf
      только показать, какие уровни границ есть в выгрузке — чтобы настроить новую страну.

  boundaries.osm.pbf — страна с Geofabrik, где оставлены только границы
                       (osmium tags-filter country.osm.pbf r/boundary=administrative)

Что получается в папке out (для каждой страны, <код> — маленькими буквами):
  <код>-index.json   — маленький общий файл: все области страны (страна, регионы, провинции, коммуны),
                       упрощённые контуры страны, регионов и провинций, как называть регионы и провинции.
  <код>-comuni.vpkg  — упрощённые (~50 м) границы всех коммун: по ним приложение видит,
                       в каких коммунах ты был.

Уровни в приложении всегда одни: 2 — страна, 4 — регион, 6 — провинция, 8 — коммуна.
В OSM у каждой страны свои (у Хорватии коммуны — 7, у Чехии регионы — 6), поэтому здесь они
переводятся. Где у провинции или региона нет коммун в данных (Берлин, кантоны-города),
сама область считается и коммуной: номер — тот же со знаком минус.

Формат .vpkg (числа little-endian):
  "VPKG", версия UInt32 (=1), число записей UInt32
  записи: вид UInt8 (0 — границы), номер Int64, начало UInt64, размер в файле UInt32,
          размер после распаковки UInt32
  дальше сами записи, каждая сжата (raw DEFLATE, как NSData .zlib в iOS)

Границы — формат VARE версии 1:
  "VARE", версия UInt32, число областей UInt32
  каждая область: номер Int64, номер родителя Int64 (0 — нет), уровень UInt8,
                  название (UInt16 длина + UTF-8), число колец UInt32,
                  каждое кольцо: число точек UInt32, точки Int32 широта/долгота × 1 000 000
                  (первая точка как есть, остальные — разницей от предыдущей)
"""
import json
import os
import struct
import sys
import time
import traceback
import zlib
from collections import Counter

import numpy as np
import osmium
import pycountry
import shapely
from shapely import STRtree

INDEX_VERSION = 3
OUTLINE_TOLERANCE = 0.002          # градусы (~200 м): упрощение контуров в общем файле
COMUNI_TOLERANCE = 0.0005          # градусы (~50 м): упрощение границ коммун в файле коммун
MAIN_DISTANCE = 3.0                # градусы: части страны дальше этого от главной — «за морем» (для камеры)
# Определение уровней. Коммуны — первый из этих уровней, что покрывает почти всю страну:
# 8 — коммуны в большинстве стран, 7 — в Японии и Скандинавии, 6 — округа в США, районы в России.
MUNICIPALITY_LEVELS = [8, 7, 6, 9, 10, 5]
PROVINCE_LEVELS = [6, 5, 7]
MIN_COVERAGE = 0.8                 # доля площади страны, которую должен покрыть уровень
MAX_MUNICIPALITIES = 80_000


class BuildError(Exception):
    pass


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def load_config():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "countries.json")
    with open(path) as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ---------- Границы из выгрузки ----------

def read_areas(path):
    """Все административные границы, собранные osmium в многоугольники."""
    areas = []
    processor = (osmium.FileProcessor(path)
                 .with_areas(osmium.filter.TagFilter(("boundary", "administrative")))
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)))
    for area in processor:
        if area.from_way():
            continue               # у линий свои номера, они могут совпасть с номерами отношений
        level = area.tags.get("admin_level", "")
        name = area.tags.get("name")
        if not name or not level.isdigit():
            continue
        polygons = []
        for outer in area.outer_rings():
            shell = [(n.lon, n.lat) for n in outer]
            holes = [[(n.lon, n.lat) for n in inner] for inner in area.inner_rings(outer)]
            if len(shell) >= 4:
                polygons.append(shapely.Polygon(shell, holes))
        if not polygons:
            continue
        geom = polygons[0] if len(polygons) == 1 else shapely.MultiPolygon(polygons)
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        areas.append({
            "id": area.orig_id(), "name": name, "level": int(level), "parentID": None,
            "iso": (area.tags.get("ISO3166-1") or area.tags.get("ISO3166-1:alpha2") or "").upper(),
            "iso2": (area.tags.get("ISO3166-2") or "").upper(),
            "geom": geom,
        })
    return areas


def probe(areas):
    """Что есть в выгрузке: сколько областей каждого уровня, их коды ISO3166-2 и примеры."""
    by_level = {}
    for a in areas:
        by_level.setdefault(a["level"], []).append(a)
    for level in sorted(by_level):
        items = by_level[level]
        prefixes = Counter(a["iso2"].split("-")[0] if a["iso2"] else "-" for a in items)
        examples = ", ".join(sorted(a["name"] for a in items)[:5])
        log(f"  level {level}: {len(items)} areas · ISO3166-2 {dict(prefixes.most_common(6))} · {examples}")
        countries = [f"{a['name']} [{a['iso']}]" for a in items if a["iso"]]
        if countries:
            log(f"    countries: {', '.join(countries[:12])}")


# ---------- Страна по уровням ----------

def country_id(code):
    """Свой номер страны: отрицательный и огромный, чтобы не совпасть ни с номером OSM,
    ни с «коммуной из провинции» (−номер OSM). Одинаковый при каждой сборке."""
    return -(10**12 + ord(code[0]) * 1000 + ord(code[1]))


def attach(children, parents):
    """Родитель области — та, в которой лежит точка внутри неё. Возвращает тех, кому нашёлся родитель."""
    if not children or not parents:
        return []
    tree = STRtree([p["geom"] for p in parents])
    points = [shapely.point_on_surface(c["geom"]) for c in children]
    child_index, parent_index = tree.query(points, predicate="within")
    found = []
    seen = set()
    for c, p in zip(child_index.tolist(), parent_index.tolist()):
        if c in seen:
            continue
        seen.add(c)
        children[c]["parentID"] = parents[p]["id"]
        found.append(children[c])
    return found


def outermost(candidates):
    """Без областей, которые лежат внутри других из этого же списка (регион и его же часть
    уровнем ниже). У двух одинаковых областей остаётся одна."""
    if len(candidates) < 2:
        return candidates
    tree = STRtree([c["geom"] for c in candidates])
    points = [shapely.point_on_surface(c["geom"]) for c in candidates]
    inner, outer = tree.query(points, predicate="within")
    nested = set()
    for i, o in zip(inner.tolist(), outer.tolist()):
        if i != o and (candidates[i]["level"], i) > (candidates[o]["level"], o):
            nested.add(i)
    return [c for k, c in enumerate(candidates) if k not in nested]


def pseudo_municipality(area):
    """Область без коммун в данных считается коммуной сама (Берлин, кантон-город)."""
    return {"id": -area["id"], "name": area["name"], "level": 8, "parentID": area["id"],
            "geom": area["geom"], "iso": "", "iso2": ""}


# Где официальное название ISO неудобно в списке стран.
SHORT_NAMES = {
    "RU": "Russia", "BN": "Brunei", "VA": "Vatican City", "FM": "Micronesia", "CD": "DR Congo",
    "CG": "Congo", "PS": "Palestine", "MO": "Macau", "VG": "British Virgin Islands",
    "VI": "U.S. Virgin Islands", "KN": "Saint Kitts and Nevis", "SH": "Saint Helena",
}


def english_name(code):
    """Название страны по-английски: «Russia», а не «Russian Federation»."""
    if code in SHORT_NAMES:
        return SHORT_NAMES[code]
    country = pycountry.countries.get(alpha_2=code)
    if country is None:
        return code
    return getattr(country, "common_name", None) or country.name


def auto_config(all_areas, code):
    """Как устроена страна, если её нет в countries.json. Смотрим только области внутри страны:
    внутри её границы (admin_level 2), а если её в выгрузке не собрать — внутри регионов с кодом ISO.
      регионы    — уровень 3–6, где больше всего областей с кодом ISO3166-2 «<код>-…»;
      коммуны    — первый из MUNICIPALITY_LEVELS, который покрывает ≥ 80% страны;
      провинции  — уровень между ними (сначала 6), тоже покрывающий страну.
    Печатает таблицу уровней — по ней видно, верно ли угадано."""
    prefix = code + "-"
    official = [a for a in all_areas if a["level"] == 2 and a["iso"] == code]
    tagged = [a for a in all_areas if 3 <= a["level"] <= 6 and a["iso2"].startswith(prefix)]
    if official:
        inside, inside_geom = "country", official[0]["geom"]
    elif tagged:
        inside, inside_geom = "regions", shapely.union_all([a["geom"] for a in outermost(tagged)])
    else:
        raise BuildError(f"no country boundary (ISO3166-1={code}) and no regions with ISO3166-2={prefix}…")

    shapely.prepare(inside_geom)
    candidates = [a for a in own_areas(all_areas, code) if 3 <= a["level"] <= 10]
    points = [shapely.point_on_surface(a["geom"]) for a in candidates]
    within = shapely.contains(inside_geom, points) if candidates else []
    count, area, iso = {}, {}, {}
    for a, ok in zip(candidates, within):
        if not ok:
            continue
        level = a["level"]
        count[level] = count.get(level, 0) + 1
        area[level] = area.get(level, 0.0) + a["geom"].area
        if a["iso2"].startswith(prefix):
            iso[level] = iso.get(level, 0) + 1

    tagged_levels = [l for l in sorted(iso) if 3 <= l <= 6 and iso[l] >= 2]
    if tagged_levels:
        region = max(tagged_levels, key=lambda l: (iso[l], -l))
    else:
        region = next((l for l in sorted(count) if 3 <= l <= 6 and count[l] >= 2), None)
    reference = area.get(region) or max(area.values(), default=0) or 1.0

    def coverage(level):
        return area.get(level, 0.0) / reference

    base = region or 2
    municipality = next((l for l in MUNICIPALITY_LEVELS
                         if l > base and l in count and count[l] >= max(2, count.get(region, 0))
                         and count[l] <= MAX_MUNICIPALITIES and coverage(l) >= MIN_COVERAGE), None)
    province = None
    if municipality:
        province = next((l for l in PROVINCE_LEVELS
                         if base < l < municipality and l in count
                         and count.get(region, 0) < count[l] < count[municipality]
                         and coverage(l) >= MIN_COVERAGE), None)

    for level in sorted(count):
        mark = {region: "region", province: "province", municipality: "municipality"}.get(level, "")
        log(f"  {code} level {level}: {count[level]} inside, covers {coverage(level):.0%}, "
            f"ISO {iso.get(level, 0)}  {mark}")
    if inside == "regions" and region not in tagged_levels:
        raise BuildError("regions have no ISO3166-2 codes and the country boundary is not in the extract")
    return {"name": english_name(code), "region": region, "province": province,
            "municipality": municipality, "inside": inside, "names": {}}


def own_areas(all_areas, code):
    """Без областей с кодом другой страны. У Австралии внутри её границы лежат остров Рождества
    и Кокосовые острова — у них свой код ISO 3166-1, это отдельные страны в списке. Регион с кодом
    ISO 3166-2 другой страны (UA-43 внутри границы России в OSM) относим к стране его кода.
    Если на то же место в OSM есть ещё и регион без кода (Крым нарисован дважды: с кодом Украины и
    без кода, внутри границы России), регион без кода тоже не берём: его середина лежит в регионе
    с чужим кодом. Только для регионов и провинций — анклавы-коммуны (Кампионе-д'Италия внутри
    Швейцарии) остаются своей стране."""
    prefix = code + "-"
    mine = [a for a in all_areas
            if (not a["iso"] or a["iso"] == code) and (not a["iso2"] or a["iso2"].startswith(prefix))]
    foreign = [a["geom"] for a in all_areas
               if a["iso2"] and not a["iso2"].startswith(prefix) and 3 <= a["level"] <= 6]
    big = [k for k, a in enumerate(mine) if 3 <= a["level"] <= 6 and not a["iso2"]]
    if not foreign or not big:
        return mine
    tree = STRtree(foreign)
    points = [shapely.point_on_surface(mine[k]["geom"]) for k in big]
    hit, _ = tree.query(points, predicate="within")
    claimed = {big[i] for i in hit.tolist()}
    return [a for k, a in enumerate(mine) if k not in claimed]


def build_hierarchy(all_areas, code, config):
    areas = [dict(a) for a in own_areas(all_areas, code)]   # копии: родителей проставляем у каждой страны свои
    prefix = code + "-"
    region_levels = as_list(config.get("region"))
    province_level = config.get("province")
    municipality_level = config.get("municipality")
    inside = config.get("inside", "regions")

    def tagged(level_list):
        return [a for a in areas if a["level"] in level_list and a["iso2"].startswith(prefix)]

    # 1. Где страна. Границу самой страны берём только если иначе нельзя: у неё есть морская часть
    #    далеко от берега, и в выгрузке её часто не собрать.
    if inside == "regions":
        regions = outermost(tagged(region_levels))
        if not regions:
            raise BuildError(f"no areas at admin_level {region_levels} with ISO3166-2={prefix}…")
        land = shapely.union_all([r["geom"] for r in regions])
    elif inside == "municipalities":
        regions = []
        marked = tagged([municipality_level])
        if not marked:
            raise BuildError(f"no municipalities at admin_level {municipality_level} with ISO3166-2={prefix}…")
        land = shapely.union_all([m["geom"] for m in marked])
    elif inside == "country":
        regions = []
        official = [a for a in areas if a["level"] == 2 and a["iso"] == code]
        if not official:
            raise BuildError(f"the country boundary (ISO3166-1={code}) is not in the extract")
        land = official[0]["geom"]
    else:
        raise BuildError(f"unknown inside={inside}")

    country = {"id": country_id(code), "name": config["name"], "level": 2, "parentID": None,
               "geom": land, "iso": code, "iso2": ""}

    # 2. Регионы (если не они задали страну — те, что внутри неё), провинции, коммуны.
    if inside != "regions" and region_levels:
        candidates = [a for a in areas if a["level"] in region_levels]
        regions = outermost(attach(candidates, [country]))
    for r in regions:
        r["parentID"] = country["id"]
    tops = regions or [country]

    provinces = []
    if province_level is not None:
        provinces = attach([a for a in areas if a["level"] == province_level], tops)

    municipalities_all = [a for a in areas if a["level"] == municipality_level] if municipality_level else []
    municipalities = attach(municipalities_all, provinces)
    # Коммуны вне провинций (Валле-д'Аоста, где провинций нет) — сразу к региону или стране.
    orphans = [m for m in municipalities_all if m["parentID"] is None]
    municipalities += attach(orphans, tops)

    # 3. Области, у которых коммун в данных нет, — сами себе коммуны.
    with_children = {m["parentID"] for m in municipalities}
    with_provinces = {p["parentID"] for p in provinces}
    for p in provinces:
        if p["id"] not in with_children:
            municipalities.append(pseudo_municipality(p))
    for r in regions:
        if r["id"] not in with_children and r["id"] not in with_provinces:
            municipalities.append(pseudo_municipality(r))
    if not municipalities:
        municipalities.append(pseudo_municipality(country))     # страна без коммун (Монако)

    # Граница страны в OSM идёт по морю (территориальные воды): для карты и камеры страна —
    # это её суша, то есть регионы, а если их нет — коммуны.
    if inside == "country":
        parts = regions or [m for m in municipalities if m["id"] > 0]
        if parts:
            country["geom"] = shapely.union_all([part["geom"] for part in parts])

    for r in regions:
        r["level"] = 4
    for p in provinces:
        p["level"] = 6
    for m in municipalities:
        m["level"] = 8
    return country, regions, provinces, municipalities


# ---------- Файлы ----------

def areas_file(areas, tolerance=0.0):
    """Границы областей в формате VARE: точные или упрощённые (tolerance в градусах)."""
    out = bytearray(b"VARE") + struct.pack("<II", 1, len(areas))
    for a in areas:
        raw = a["name"].encode("utf-8")
        rings = []
        geom = a["geom"]
        if tolerance > 0:
            geom = shapely.simplify(geom, tolerance, preserve_topology=True)
        for polygon in shapely.get_parts(geom):
            if polygon.is_empty or polygon.geom_type != "Polygon":
                continue
            for ring in [polygon.exterior, *polygon.interiors]:
                rings.append(np.asarray(ring.coords))
        out += struct.pack("<qqB", a["id"], a["parentID"] or 0, a["level"])
        out += struct.pack("<H", len(raw)) + raw + struct.pack("<I", len(rings))
        for coords in rings:
            lat = np.round(coords[:, 1] * 1_000_000).astype(np.int64)
            lon = np.round(coords[:, 0] * 1_000_000).astype(np.int64)
            points = np.empty(2 * len(coords), dtype="<i4")
            points[0::2] = np.diff(lat, prepend=0)
            points[1::2] = np.diff(lon, prepend=0)
            out += struct.pack("<I", len(coords)) + points.tobytes()
    return bytes(out)


def outline(geom):
    """Упрощённый контур для общего файла: [[ [широта, долгота], … ], …]."""
    simple = shapely.simplify(geom, OUTLINE_TOLERANCE, preserve_topology=True)
    rings = []
    for polygon in shapely.get_parts(simple):
        if polygon.is_empty or polygon.geom_type != "Polygon":
            continue
        for ring in [polygon.exterior, *polygon.interiors]:
            rings.append([[round(y, 5), round(x, 5)] for x, y in ring.coords])
    return rings


def main_box(geom):
    """Рамка основной части страны, без далёких островов и заморских земель: для камеры.
    [мин. широта, мин. долгота, макс. широта, макс. долгота]. Сравниваем рамки, а не сами
    контуры: точное расстояние от тысячи островов до берега в миллион точек считается часами."""
    bounds = [p.bounds for p in shapely.get_parts(geom) if not p.is_empty]
    areas = [(b[2] - b[0]) * (b[3] - b[1]) for b in bounds]
    x0, y0, x1, y1 = bounds[areas.index(max(areas))]

    def gap(b):
        dx = max(x0 - b[2], b[0] - x1, 0.0)
        dy = max(y0 - b[3], b[1] - y1, 0.0)
        return (dx * dx + dy * dy) ** 0.5

    near = [b for b in bounds if gap(b) <= MAIN_DISTANCE]
    west, south = min(b[0] for b in near), min(b[1] for b in near)
    east, north = max(b[2] for b in near), max(b[3] for b in near)
    return [round(south, 4), round(west, 4), round(north, 4), round(east, 4)]


def deflate(data):
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)     # raw DEFLATE, без заголовков
    return packer.compress(data) + packer.flush()


def write_package(path, entries):
    """entries: список (вид, номер, данные). Каждая запись сжимается отдельно."""
    blobs = [(kind, ident, deflate(data), len(data)) for kind, ident, data in entries]
    header_size = 12 + len(blobs) * (1 + 8 + 8 + 4 + 4)
    out = bytearray(b"VPKG") + struct.pack("<II", 1, len(blobs))
    offset = header_size
    for kind, ident, blob, raw in blobs:
        out += struct.pack("<BqQII", kind, ident, offset, len(blob), raw)
        offset += len(blob)
    for _, _, blob, _ in blobs:
        out += blob
    with open(path, "wb") as f:
        f.write(out)
    return len(out)


def level_names(config, regions, provinces):
    """Как называть регионы и провинции этой страны в приложении."""
    names = config.get("names", {})
    result = []
    if regions:
        one, many = names.get("region", ["Region", "Regions"])
        result.append({"level": 4, "one": one, "many": many})
    if provinces:
        one, many = names.get("province", ["District", "Districts"])
        result.append({"level": 6, "one": one, "many": many, "prefix": bool(config.get("prefix"))})
    one, many = names.get("municipality", ["Municipality", "Municipalities"])
    result.append({"level": 8, "one": one, "many": many})
    return result


def build(all_areas, code, config, out_dir):
    country, regions, provinces, municipalities = build_hierarchy(all_areas, code, config)
    pseudo = sum(1 for m in municipalities if m["id"] < 0)
    log(f"{code} {country['name']}: {len(regions)} regions, {len(provinces)} provinces, "
        f"{len(municipalities)} municipalities ({pseudo} of them whole regions or provinces)")
    if regions:
        log(f"  regions: {', '.join(sorted(r['name'] for r in regions))}")

    lower = code.lower()
    comuni_name = f"{lower}-comuni.vpkg"
    comuni_size = write_package(os.path.join(out_dir, comuni_name),
                                [(0, country["id"], areas_file(municipalities, COMUNI_TOLERANCE))])

    index_areas = []
    for a in [country, *regions, *provinces, *municipalities]:
        item = {"id": a["id"], "name": a["name"], "level": a["level"], "parentID": a["parentID"]}
        if a["level"] in (2, 4, 6):
            item["outline"] = outline(a["geom"])
        index_areas.append(item)
    index = {"version": INDEX_VERSION, "country": code, "name": country["name"],
             "date": time.strftime("%Y-%m-%d", time.gmtime()),
             "comuniFile": comuni_name, "comuniFileSize": comuni_size,
             "box": main_box(country["geom"]),
             "levels": level_names(config, regions, provinces),
             "areas": index_areas}
    index_path = os.path.join(out_dir, f"{lower}-index.json")
    with open(index_path, "w") as f:
        json.dump(index, f, separators=(",", ":"), ensure_ascii=False)
    log(f"  {comuni_name} {comuni_size / 1e6:.1f} MB, index {os.path.getsize(index_path) / 1e6:.1f} MB")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--probe":
        log(f"reading boundaries from {args[1]}")
        probe(read_areas(args[1]))
        return
    source, out_dir, codes = args[0], args[1], [c.upper() for c in args[2:]]
    config = load_config()
    os.makedirs(out_dir, exist_ok=True)

    log("reading boundaries")
    areas = read_areas(source)
    log(f"{len(areas)} areas; what the extract has:")
    probe(areas)

    failed = []
    for code in codes:
        try:
            settings = config.get(code)
            if settings is None or "municipality" not in settings:
                found = auto_config(areas, code)
                found.update(settings or {})          # из countries.json — только поправки (название, имена уровней)
                settings = found
            settings.setdefault("name", english_name(code))
            build(areas, code, settings, out_dir)
        except Exception as error:          # одна страна не собралась — остальные из этой выгрузки всё равно
            log(f"{code} FAILED: {error}")
            if not isinstance(error, BuildError):
                traceback.print_exc()
            failed.append(code)
    if failed:
        sys.exit(f"failed: {' '.join(failed)}")


if __name__ == "__main__":
    main()
