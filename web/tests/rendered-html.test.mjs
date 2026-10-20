import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("client bundle and manifest exist after build", async () => {
  const manifestUrl = new URL("../dist/client/vinext-client-entry-manifest.json", import.meta.url);
  const manifestContent = await readFile(manifestUrl, "utf8");
  const manifest = JSON.parse(manifestContent);
  assert.ok(manifest, "Manifest should be valid JSON");
});

test("page component contains Dossel Ambiental UI elements", async () => {
  const pageSrc = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(pageSrc, /Dossel Ambiental/);
  assert.match(pageSrc, /MapViewer/);
  assert.match(pageSrc, /Visualizar/);
  assert.match(pageSrc, /Ordem Hack/);
});

test("map component has Leaflet and GeoParquet vector handling", async () => {
  const mapSrc = await readFile(new URL("../app/components/MapViewer.tsx", import.meta.url), "utf8");
  assert.match(mapSrc, /MapContainer/);
  assert.match(mapSrc, /GeoJSON/);
  assert.match(mapSrc, /hack_order/);
});
