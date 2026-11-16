# viision-data

Country boundaries for the viision app, built from OpenStreetMap.

Once a month GitHub Actions downloads each country from Geofabrik, keeps administrative boundaries,
and publishes ready files in Releases (tag `maps`). In the app, people download only the countries they want.

- `catalog.json` — the list of countries: name, size, number of municipalities, a coarse outline
- `<code>-index.json` — all areas of a country (regions, provinces, municipalities) with simplified outlines
- `<code>-comuni.vpkg` — simplified boundaries of all municipalities (to see where you have been)

How each country is split into regions, provinces and municipalities is set in `countries.json`.
To add a country: run the workflow with `probe` set to its Geofabrik path (for example `europe/greece`),
look at the boundary levels in the log, add it to `countries.json`, and run the workflow with its code.

The old `italy` release is used by earlier builds of the app and is no longer updated.

Data © OpenStreetMap contributors, available under the Open Database License (ODbL).
