"use client";

import dynamic from "next/dynamic";
import {
  ArrowDownToLine,
  Check,
  ChevronDown,
  Database,
  FileBox,
  FolderOpen,
  Eye,
  Layers3,
  LoaderCircle,
  Map,
  Play,
  RotateCcw,
  SlidersHorizontal,
  TriangleAlert,
  UploadCloud,
  Waves,
} from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

const MapViewer = dynamic(() => import("./components/MapViewer"), {
  ssr: false,
  loading: () => <div className="map-loading">Preparing map canvas…</div>,
});

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

type Artifact = {
  name: string;
  kind: "raster" | "vector" | "metadata";
  size: number;
  download_url: string;
  preview_url: string | null;
  info_url: string | null;
};

type Job = {
  id: string;
  status: "queued" | "processing" | "complete" | "failed";
  stage: string;
  progress: number;
  file_count: number;
  total_bytes: number;
  error: string | null;
  artifacts: Artifact[];
};

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function artifactLabel(name: string) {
  return name
    .replace(/\.(tif|tiff|parquet|json)$/i, "")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export default function Home() {
  const picker = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [folderName, setFolderName] = useState("");
  const [threshold, setThreshold] = useState("10000");
  const [thresholdUnits, setThresholdUnits] = useState<"cells" | "area">("cells");
  const [breachDistance, setBreachDistance] = useState("100");
  const [minStreamLength, setMinStreamLength] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [inspected, setInspected] = useState<Artifact | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [serverReady, setServerReady] = useState<boolean | null>(null);
  const [requestError, setRequestError] = useState("");
  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    picker.current?.setAttribute("webkitdirectory", "");
    picker.current?.setAttribute("directory", "");
    fetch(`${API_BASE}/api/health`)
      .then((response) => response.json())
      .then((health) => setServerReady(health.status === "ready"))
      .catch(() => setServerReady(false));
  }, []);

  useEffect(() => {
    if (!jobId || !jobStatus || !["queued", "processing"].includes(jobStatus)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/api/jobs/${jobId}`);
        const next = (await response.json()) as Job;
        setJob(next);
      } catch {
        setRequestError("The processing service stopped responding.");
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [jobId, jobStatus]);

  const totalSize = useMemo(() => files.reduce((sum, file) => sum + file.size, 0), [files]);
  const viewable = job?.artifacts.filter((artifact) => artifact.kind !== "metadata") || [];

  function acceptFiles(incoming: File[]) {
    const dems = incoming.filter((file) => /\.tiff?$/i.test(file.name));
    setFiles(dems);
    const relative = dems[0]?.webkitRelativePath;
    setFolderName(relative ? relative.split("/")[0] : dems.length ? "Selected DEMs" : "");
    setJob(null);
    setSelected(null);
    setInspected(null);
    setRequestError(dems.length ? "" : "No .tif or .tiff files were found in that folder.");
  }

  function onFolderChange(event: ChangeEvent<HTMLInputElement>) {
    acceptFiles(Array.from(event.target.files || []));
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    acceptFiles(Array.from(event.dataTransfer.files));
  }

  function startProcessing() {
    if (!files.length || uploading) return;
    setUploading(true);
    setUploadProgress(0);
    setRequestError("");
    setSelected(null);
    setInspected(null);
    const data = new FormData();
    files.forEach((file) => data.append("files", file, file.name));
    data.append("threshold", threshold);
    data.append("threshold_units", thresholdUnits);
    data.append("breach_distance", breachDistance);
    if (minStreamLength) data.append("min_stream_length", minStreamLength);

    const request = new XMLHttpRequest();
    request.open("POST", `${API_BASE}/api/jobs`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) setUploadProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onload = () => {
      setUploading(false);
      try {
        const payload = JSON.parse(request.responseText);
        if (request.status >= 200 && request.status < 300) setJob(payload);
        else setRequestError(payload.detail || "The processing job could not be created.");
      } catch {
        setRequestError("The processing service returned an invalid response.");
      }
    };
    request.onerror = () => {
      setUploading(false);
      setRequestError("Could not connect to the local processing service.");
    };
    request.send(data);
  }

  function reset() {
    setFiles([]);
    setFolderName("");
    setJob(null);
    setSelected(null);
    setInspected(null);
    setUploadProgress(0);
    setRequestError("");
    if (picker.current) picker.current.value = "";
  }

  const processing = uploading || job?.status === "queued" || job?.status === "processing";
  const progress = uploading ? uploadProgress : job?.progress || 0;
  const stage = uploading ? "Uploading DEM tiles" : job?.stage;

  return (
    <main className="app-shell">
      <aside className="control-panel">
        <header className="brand">
          <span className="brand-mark"><Waves size={20} strokeWidth={2.4} /></span>
          <div><strong>Drainage Studio</strong><span>Terrain network lab</span></div>
          <span className={`service-dot ${serverReady ? "online" : ""}`} title={serverReady ? "Processor online" : "Processor offline"} />
        </header>

        <section className="intro">
          <p className="eyebrow">DEM → ordered network</p>
          <h1>Reveal how the terrain drains.</h1>
          <p>Select a DEM folder, run a Whitebox hydrology workflow, then inspect every raster and GeoParquet result on the map.</p>
        </section>

        <div className="steps" aria-label="Workflow progress">
          {["Source", "Process", "Explore"].map((label, index) => {
            const active = index === 0 || (index === 1 && !!job) || (index === 2 && job?.status === "complete");
            return <div className={active ? "step active" : "step"} key={label}><span>{active && index < 2 && job?.status === "complete" ? <Check size={12} /> : index + 1}</span>{label}</div>;
          })}
        </div>

        <section className="source-section">
          <div className="section-heading"><span>01</span><div><h2>Source terrain</h2><p>GeoTIFF tiles with a shared CRS</p></div></div>
          <input ref={picker} className="sr-only" type="file" multiple accept=".tif,.tiff" onChange={onFolderChange} />
          <div
            className={`drop-zone ${files.length ? "has-files" : ""}`}
            onClick={() => picker.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => event.key === "Enter" && picker.current?.click()}
          >
            <span className="drop-icon">{files.length ? <FolderOpen size={24} /> : <UploadCloud size={24} />}</span>
            {files.length ? <><strong>{folderName}</strong><span>{files.length} DEM {files.length === 1 ? "tile" : "tiles"} · {formatBytes(totalSize)}</span><small>Click to choose another folder</small></> : <><strong>Choose a DEM folder</strong><span>or drag GeoTIFF files here</span><small>.tif and .tiff</small></>}
          </div>
        </section>

        <section className="parameters-section">
          <button className="advanced-toggle" type="button" onClick={() => setAdvancedOpen((value) => !value)} aria-expanded={advancedOpen}>
            <span><SlidersHorizontal size={16} /> Processing parameters</span><ChevronDown className={advancedOpen ? "rotated" : ""} size={16} />
          </button>
          {advancedOpen && <div className="parameter-grid">
            <label><span>Stream threshold</span><div className="input-pair"><input type="number" min="1" value={threshold} onChange={(event) => setThreshold(event.target.value)} /><select value={thresholdUnits} onChange={(event) => setThresholdUnits(event.target.value as "cells" | "area")}><option value="cells">cells</option><option value="area">map units²</option></select></div></label>
            <label><span>Breach distance</span><div className="input-suffix"><input type="number" min="1" value={breachDistance} onChange={(event) => setBreachDistance(event.target.value)} /><em>cells</em></div></label>
            <label><span>Minimum stream length</span><div className="input-suffix"><input type="number" min="0" placeholder="Optional" value={minStreamLength} onChange={(event) => setMinStreamLength(event.target.value)} /><em>map units</em></div></label>
          </div>}
        </section>

        {requestError && <div className="error-message"><TriangleAlert size={17} /><span>{requestError}</span></div>}
        {job?.status === "failed" && <div className="error-message"><TriangleAlert size={17} /><span>{job.error}</span></div>}

        {processing && <section className="progress-card">
          <div className="progress-copy"><span><LoaderCircle className="spin" size={17} /> {stage}</span><strong>{progress}%</strong></div>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <p>{uploading ? "Keeping source precision intact" : "You can leave this tab open while WhiteboxTools runs"}</p>
        </section>}

        {job?.status === "complete" && <section className="results-section">
          <div className="section-heading result-heading"><span>03</span><div><h2>Explore outputs</h2><p>{viewable.length} visual layers ready</p></div><Check className="complete-check" size={18} /></div>
          <div className="artifact-list">
            {viewable.map((artifact) => <button type="button" className={selected?.name === artifact.name ? "artifact selected" : "artifact"} onClick={() => setSelected(artifact)} key={artifact.name}>
              <span className="artifact-icon">{artifact.kind === "vector" ? <Waves size={17} /> : <Layers3 size={17} />}</span>
              <span><strong>{artifactLabel(artifact.name)}</strong><small>{artifact.kind === "vector" ? "GeoParquet" : "GeoTIFF"} · {formatBytes(artifact.size)}</small></span>
              <a href={`${API_BASE}${artifact.download_url}`} onClick={(event) => event.stopPropagation()} title={`Download ${artifact.name}`}><ArrowDownToLine size={15} /></a>
            </button>)}
          </div>
          <button className="inspect-button" type="button" disabled={!selected} onClick={() => selected && setInspected(selected)}>
            <Eye size={16} /> {selected ? `Inspect ${artifactLabel(selected.name)}` : "Select an output to inspect"}
          </button>
        </section>}

        <div className="panel-actions">
          <button className="process-button" type="button" disabled={!files.length || processing || serverReady === false} onClick={startProcessing}>{processing ? <LoaderCircle className="spin" size={18} /> : <Play size={17} fill="currentColor" />} {job?.status === "complete" ? "Process again" : "Process drainage"}</button>
          {(files.length > 0 || job) && <button className="reset-button" type="button" onClick={reset} title="Reset workflow"><RotateCcw size={17} /></button>}
        </div>
      </aside>

      <section className="workspace">
        <header className="workspace-header">
          <div><span className="workspace-kicker">Map workspace</span><h2>{inspected ? artifactLabel(inspected.name) : "Drainage preview"}</h2></div>
          <div className="format-badges"><span><Database size={13} /> GeoParquet vectors</span><span><FileBox size={13} /> GeoTIFF rasters</span></div>
        </header>
        <div className="map-frame">
          {job?.status === "complete" && inspected ? <MapViewer key={`${job.id}:${inspected.name}`} apiBase={API_BASE} jobId={job.id} artifact={inspected} /> : <div className="map-empty">
            <div className="contour contour-one" /><div className="contour contour-two" /><div className="contour contour-three" />
            <span className="map-empty-icon"><Map size={28} /></span>
            <h3>{job?.status === "complete" ? "Choose exactly what to inspect" : "Your terrain will appear here"}</h3>
            <p>{job?.status === "complete" ? "Select one generated output, then press Inspect. No file is previewed automatically." : "Choose a folder and process the DEMs to inspect the extracted drainage hierarchy."}</p>
            <div className="order-preview"><span><i className="order-1" /> Main drainage · order 1</span><span><i className="order-2" /> Direct tributaries · order 2</span><span><i className="order-3" /> Upper branches · order 3+</span></div>
          </div>}
        </div>
        <footer className="workspace-footer"><span>Hack hierarchy: lower order = closer to the main trunk</span><span>WhiteboxTools · D8 flow model</span></footer>
      </section>
    </main>
  );
}
