"use client";

import { useEffect, useMemo, useState } from "react";
import { GeoJSON, ImageOverlay, MapContainer, TileLayer, useMap } from "react-leaflet";
import type { GeoJsonObject, Feature } from "geojson";
import type { LatLngBoundsExpression, PathOptions } from "leaflet";
import { LoaderCircle, TriangleAlert } from "lucide-react";

type Artifact = { name: string; kind: "raster" | "vector" | "metadata"; preview_url: string | null; info_url: string | null };
type PreviewInfo = { name: string; kind: "raster" | "vector"; bounds: [[number, number], [number, number]]; preview_url: string };

function FitBounds({ bounds }: { bounds: LatLngBoundsExpression }) {
  const map = useMap();
  useEffect(() => { map.fitBounds(bounds, { padding: [32, 32], maxZoom: 15 }); }, [bounds, map]);
  return null;
}

const colours = ["#1a3c2f", "#2a5c3f", "#3d7a5f", "#8fbf8f", "#d3b56c", "#c4785c"];

export default function MapViewer({ apiBase, jobId, artifact }: { apiBase: string; jobId: string; artifact: Artifact }) {
  const [info, setInfo] = useState<PreviewInfo | null>(null);
  const [geojson, setGeojson] = useState<GeoJsonObject | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!artifact.info_url) return;
    const controller = new AbortController();
    fetch(`${apiBase}${artifact.info_url}`, { signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error("Não foi possível carregar os metadados da camada."); return response.json(); })
      .then(async (next: PreviewInfo) => {
        setInfo(next);
        if (next.kind === "vector") {
          const response = await fetch(`${apiBase}${next.preview_url}`, { signal: controller.signal });
          if (!response.ok) throw new Error("Não foi possível carregar a camada vetorial.");
          setGeojson(await response.json());
        }
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason.message);
      });
    return () => controller.abort();
  }, [apiBase, artifact.info_url, jobId]);

  const bounds = useMemo(() => (info?.bounds || [[-20, -30], [20, 30]]) as LatLngBoundsExpression, [info]);

  if (error) return <div className="map-state error"><TriangleAlert size={22} /><strong>Não foi possível exibir esta camada</strong><span>{error}</span></div>;
  if (!info || (info.kind === "vector" && !geojson)) return <div className="map-state"><LoaderCircle className="spin" size={24} /><strong>Carregando visualização</strong><span>Preparando a camada para o mapa…</span></div>;

  function lineStyle(feature?: Feature): PathOptions {
    const order = Number(feature?.properties?.hack_order || 1);
    return { color: colours[Math.min(Math.max(order - 1, 0), colours.length - 1)], weight: Math.max(1.4, 5.2 - order * 0.7), opacity: 0.94 };
  }

  return <div className="map-viewer">
    <MapContainer bounds={bounds} zoomControl attributionControl preferCanvas>
      <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" opacity={0.42} />
      <FitBounds bounds={bounds} />
      {info.kind === "raster" && <ImageOverlay url={`${apiBase}${info.preview_url}`} bounds={bounds} opacity={0.9} />}
      {info.kind === "vector" && geojson && <GeoJSON data={geojson} style={lineStyle} onEachFeature={(feature, layer) => {
        const order = feature.properties?.hack_order;
        const length = Number(feature.properties?.length || 0);
        layer.bindPopup(`<strong>Ordem Hack ${order}</strong><br/>Comprimento: ${length.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} unidades do mapa`);
      }} />}
    </MapContainer>
    {info.kind === "vector" && <div className="map-legend"><strong>Ordem Hack</strong><span><i style={{ background: colours[0] }} />1 · canal principal</span><span><i style={{ background: colours[1] }} />2 · tributários</span><span><i style={{ background: colours[2] }} />3</span><span><i style={{ background: colours[3] }} />4+</span></div>}
    <div className="layer-chip">{info.kind === "vector" ? "GeoParquet" : "GeoTIFF"}</div>
  </div>;
}
