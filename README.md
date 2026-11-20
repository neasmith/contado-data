# contado-data

Country borders for the Contado app, built from OpenStreetMap.

GitHub Actions downloads countries from Geofabrik, keeps only administrative boundaries,
and publishes small files in Releases (tag `maps`). In the app, people download only the countries they want.

- `catalog.json` — the list of countries: name, size, number of municipalities, a coarse outline
- `<code>-index.json` — all areas of a country (regions, provinces, municipalities) with simplified outlines
- `<code>-comuni.vpkg` — simplified borders of all municipalities

Every country from Geofabrik's list is built (`geofabrik-sources.json` keeps which extract holds each
country, in case Geofabrik's list has no country codes). The script works out by itself which OpenStreetMap
admin level is a region, a province and a municipality in each country; a region that this level does not
divide (Moscow, Berlin, Vienna) is divided by a finer level inside it. `countries.json` holds only
the exceptions and the names of the levels. Borders rarely change, so each month the workflow builds
new countries and a sixth of the others: every country is rebuilt about twice a year.

Run by hand: Actions → Build map data → Run workflow, with country codes (`IT FR`) or `ALL`.
To look at a new extract first, put its Geofabrik path (`europe/greece`) into `probe`.

Data © OpenStreetMap contributors, available under the Open Database License (ODbL).
