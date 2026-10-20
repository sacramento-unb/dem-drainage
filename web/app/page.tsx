"use client";

import dynamic from "next/dynamic";
import {
  ArrowDownToLine,
  Archive,
  Check,
  ChevronDown,
  FileBox,
  FolderOpen,
  Eye,
  Layers3,
  LoaderCircle,
  Map,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  SlidersHorizontal,
  TriangleAlert,
  Trash2,
  UploadCloud,
  Waves,
} from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

const MapViewer = dynamic(() => import("./components/MapViewer"), {
  ssr: false,
  loading: () => <div className="map-loading">Preparando o mapa…</div>,
});

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8055";

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

type OutputFile = {
  job_id: string;
  name: string;
  kind: "raster" | "vector" | "metadata";
  size: number;
  modified_at: string;
  download_url: string;
};

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function artifactLabel(name: string) {
  const labels: Record<string, string> = {
    dem: "DEM",
    breached: "depressões corrigidas",
    d8: "D8",
    pointer: "direção",
    flow: "fluxo",
    accumulation: "acumulação",
    streams: "canais",
    pruned: "filtrados",
    hack: "Hack",
    order: "ordem",
    drainage: "drenagem",
    run: "processamento",
    manifest: "metadados",
  };
  return name
    .replace(/\.(tif|tiff|parquet|json)$/i, "")
    .split("_")
    .map((part) => labels[part.toLowerCase()] || part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function fileDate(value: string) {
  return new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export default function Home() {
  const picker = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [folderName, setFolderName] = useState("");
  const [threshold, setThreshold] = useState("10000");
  const [thresholdUnits, setThresholdUnits] = useState<"cells" | "area">("cells");
  const [minStreamLength, setMinStreamLength] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [inspected, setInspected] = useState<Artifact | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [serverReady, setServerReady] = useState<boolean | null>(null);
  const [requestError, setRequestError] = useState("");
  const [workspaceTab, setWorkspaceTab] = useState<"map" | "files">("map");
  const [outputFiles, setOutputFiles] = useState<OutputFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState("");
  const [fileSearch, setFileSearch] = useState("");
  const [filesRefreshKey, setFilesRefreshKey] = useState(0);
  const [deletingFile, setDeletingFile] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
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
        setRequestError("O serviço de processamento parou de responder.");
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [jobId, jobStatus]);

  useEffect(() => {
    if (workspaceTab !== "files") return;
    fetch(`${API_BASE}/api/files`)
      .then((response) => {
        if (!response.ok) throw new Error("Não foi possível carregar os arquivos de saída.");
        return response.json() as Promise<{ files: OutputFile[] }>;
      })
      .then((payload) => setOutputFiles(payload.files))
      .catch((error) => setFilesError(error.message))
      .finally(() => setFilesLoading(false));
  }, [workspaceTab, filesRefreshKey]);

  const totalSize = useMemo(() => files.reduce((sum, file) => sum + file.size, 0), [files]);
  const viewable = job?.artifacts.filter((artifact) => artifact.kind !== "metadata") || [];
  const parquetCount = job?.artifacts.filter((artifact) => artifact.kind === "vector").length || 0;
  const visibleOutputFiles = outputFiles.filter((file) =>
    `${file.job_id} ${file.name}`.toLowerCase().includes(fileSearch.toLowerCase()),
  );
  const targetFiles = fileSearch ? visibleOutputFiles : outputFiles;
  const allFilesSelected = targetFiles.length > 0 && targetFiles.every((file) => selectedFiles.includes(`${file.job_id}/${file.name}`));

  function acceptFiles(incoming: File[]) {
    const dems = incoming.filter((file) => /\.tiff?$/i.test(file.name));
    setFiles(dems);
    const relative = dems[0]?.webkitRelativePath;
    setFolderName(relative ? relative.split("/")[0] : dems.length ? "DEMs selecionados" : "");
    setJob(null);
    setSelected(null);
    setInspected(null);
    setRequestError(dems.length ? "" : "Nenhum arquivo .tif ou .tiff foi encontrado nessa pasta.");
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
        else setRequestError(payload.detail || "Não foi possível criar o processamento.");
      } catch {
        setRequestError("O serviço de processamento retornou uma resposta inválida.");
      }
    };
    request.onerror = () => {
      setUploading(false);
      setRequestError("Não foi possível conectar ao serviço local de processamento.");
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

  function openFileManager() {
    setFilesError("");
    setFilesLoading(true);
    setFilesRefreshKey((value) => value + 1);
    setWorkspaceTab("files");
  }

  function refreshOutputFiles() {
    setFilesError("");
    setFilesLoading(true);
    setFilesRefreshKey((value) => value + 1);
  }

  function toggleAllFiles() {
    if (allFilesSelected) {
      const targetKeys = new Set(targetFiles.map((file) => `${file.job_id}/${file.name}`));
      setSelectedFiles((current) => current.filter((key) => !targetKeys.has(key)));
    } else {
      const targetKeys = targetFiles.map((file) => `${file.job_id}/${file.name}`);
      setSelectedFiles((current) => Array.from(new Set([...current, ...targetKeys])));
    }
  }

  function toggleFile(file: OutputFile) {
    const key = `${file.job_id}/${file.name}`;
    setSelectedFiles((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  }

  async function deleteOutputFile(file: OutputFile) {
    if (!window.confirm(`Excluir ${file.name}? Esta ação não pode ser desfeita.`)) return;
    setDeletingFile(`${file.job_id}/${file.name}`);
    setFilesError("");
    try {
      const response = await fetch(`${API_BASE}${file.download_url}`, { method: "DELETE" });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail || "Não foi possível excluir o arquivo de saída.");
      }
      setSelectedFiles((current) => current.filter((item) => item !== `${file.job_id}/${file.name}`));
      setFilesRefreshKey((value) => value + 1);
    } catch (error) {
      setFilesError(error instanceof Error ? error.message : "Não foi possível excluir o arquivo de saída.");
    } finally {
      setDeletingFile("");
    }
  }

  async function deleteSelectedFiles() {
    if (!selectedFiles.length || !window.confirm(`Excluir ${selectedFiles.length} arquivo(s) selecionado(s)? Esta ação não pode ser desfeita.`)) return;
    setDeletingFile("bulk");
    setFilesError("");
    try {
      const filesToDelete = outputFiles.filter((file) => selectedFiles.includes(`${file.job_id}/${file.name}`));
      const response = await fetch(`${API_BASE}/api/files/bulk-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          files: filesToDelete.map((file) => ({ job_id: file.job_id, name: file.name })),
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(payload?.detail || "Não foi possível excluir alguns arquivos.");
      }
      setSelectedFiles([]);
      setFilesRefreshKey((value) => value + 1);
    } catch (error) {
      setFilesError(error instanceof Error ? error.message : "Não foi possível excluir alguns arquivos.");
    } finally {
      setDeletingFile("");
    }
  }

  const processing = uploading || job?.status === "queued" || job?.status === "processing";
  const progress = uploading ? uploadProgress : job?.progress || 0;
  const stage = uploading ? "Enviando arquivos DEM" : job?.stage;

  return (
    <main className="app-shell">
      <aside className="control-panel">
        <header className="brand">
          <span className="brand-mark"><Waves size={20} strokeWidth={2.4} /></span>
          <div><strong>Dossel Ambiental</strong><span>Estúdio de drenagem</span></div>
          <span className={`service-dot ${serverReady ? "online" : ""}`} title={serverReady ? "Processador online" : "Processador offline"} />
        </header>

        <section className="intro">
          <p className="eyebrow">DEM → rede de drenagem</p>
          <h1>Descubra como o terreno drena.</h1>
          <p>Selecione os arquivos DEM, processe o fluxo hidrológico no WhiteboxTools e visualize os rasters e GeoParquets gerados no mapa.</p>
        </section>

        <div className="steps" aria-label="Progresso do fluxo">
          {["Dados de entrada", "Processamento", "Resultados"].map((label, index) => {
            const active = index === 0 || (index === 1 && !!job) || (index === 2 && job?.status === "complete");
            return <div className={active ? "step active" : "step"} key={label}><span>{active && index < 2 && job?.status === "complete" ? <Check size={12} /> : index + 1}</span>{label}</div>;
          })}
        </div>

        <section className="source-section">
          <div className="section-heading"><span>01</span><div><h2>Dados de entrada</h2><p>Arquivos GeoTIFF com o mesmo SRC</p></div></div>
          <input
            ref={picker}
            className="sr-only"
            type="file"
            multiple
            accept=".tif,.tiff"
            onChange={onFolderChange}
          />
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
            {files.length ? <><strong>{folderName}</strong><span>{files.length} {files.length === 1 ? "arquivo DEM" : "arquivos DEM"} · {formatBytes(totalSize)}</span><small>Clique para trocar os arquivos</small></> : <><strong>Selecione os arquivos DEM</strong><span>ou arraste-os para esta área</span><small>Arquivos aceitos: .tif e .tiff</small></>}
          </div>
        </section>

        <section className="parameters-section">
          <button className="advanced-toggle" type="button" onClick={() => setAdvancedOpen((value) => !value)} aria-expanded={advancedOpen}>
            <span><SlidersHorizontal size={16} /> Parâmetros de processamento</span><ChevronDown className={advancedOpen ? "rotated" : ""} size={16} />
          </button>
          {advancedOpen && <div className="parameter-grid">
            <label><span>Limiar para iniciar os canais</span><div className="input-pair"><input type="number" min="1" value={threshold} onChange={(event) => setThreshold(event.target.value)} /><select value={thresholdUnits} onChange={(event) => setThresholdUnits(event.target.value as "cells" | "area")}><option value="cells">células</option><option value="area">unidades² do mapa</option></select></div></label>
            <label><span>Comprimento mínimo dos canais</span><div className="input-suffix"><input type="number" min="0" placeholder="Opcional" value={minStreamLength} onChange={(event) => setMinStreamLength(event.target.value)} /><em>unidades do mapa</em></div></label>
          </div>}
        </section>

        {requestError && <div className="error-message"><TriangleAlert size={17} /><span>{requestError}</span></div>}
        {job?.status === "failed" && <div className="error-message"><TriangleAlert size={17} /><span>{job.error}</span></div>}

        {processing && <section className="progress-card">
          <div className="progress-copy"><span><LoaderCircle className="spin" size={17} /> {stage}</span><strong>{progress}%</strong></div>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <p>{uploading ? "Os arquivos estão sendo enviados sem alteração" : "O processamento continua enquanto esta aba estiver aberta"}</p>
        </section>}

        {job?.status === "complete" && <section className="results-section">
          <div className="section-heading result-heading"><span>03</span><div><h2>Resultados</h2><p>{viewable.length} camadas disponíveis para visualização</p></div><Check className="complete-check" size={18} /></div>
          <div className="artifact-list">
            {viewable.map((artifact) => <div className={selected?.name === artifact.name ? "artifact selected" : "artifact"} key={artifact.name}>
              <button type="button" className="artifact-select" onClick={() => setSelected(artifact)}>
                <span className="artifact-icon">{artifact.kind === "vector" ? <Waves size={17} /> : <Layers3 size={17} />}</span>
                <span><strong>{artifactLabel(artifact.name)}</strong><small>{artifact.kind === "vector" ? "GeoParquet" : "GeoTIFF"} · {formatBytes(artifact.size)}</small></span>
              </button>
              <a className={artifact.kind === "vector" ? "artifact-download parquet-download" : "artifact-download"} href={`${API_BASE}${artifact.download_url}`} download={artifact.name} title={`Baixar ${artifact.name}`}>
                <ArrowDownToLine size={15} />{artifact.kind === "vector" && <span>Baixar arquivo</span>}
              </a>
            </div>)}
          </div>
          {parquetCount > 0 && <a className="bulk-download" href={`${API_BASE}/api/jobs/${job.id}/geoparquets.zip`} download={`${job.id}_geoparquets.zip`}>
            <Archive size={16} /> Baixar arquivos GeoParquet ({parquetCount})
          </a>}
          <button className="inspect-button" type="button" disabled={!selected} onClick={() => selected && setInspected(selected)}>
            <Eye size={16} /> {selected ? `Visualizar ${artifactLabel(selected.name)}` : "Selecione uma camada para visualizar"}
          </button>
        </section>}

        <div className="panel-actions">
          <button
            className="process-button"
            type="button"
            disabled={!files.length || processing || serverReady === false}
            title={!files.length ? "Selecione os arquivos DEM para começar" : serverReady === false ? "O serviço de processamento está indisponível" : undefined}
            onClick={startProcessing}
          >
            {processing ? <LoaderCircle className="spin" size={18} /> : <Play size={17} fill="currentColor" />} {job?.status === "complete" ? "Executar novamente" : "Gerar rede de drenagem"}
          </button>
          {(files.length > 0 || job) && <button className="reset-button" type="button" onClick={reset} title="Limpar processamento"><RotateCcw size={17} /></button>}
        </div>
      </aside>

      <section className="workspace">
        <header className="workspace-header">
          <div><span className="workspace-kicker">Dossel · análise de drenagem</span><h2>{workspaceTab === "files" ? "Gerenciador de arquivos" : inspected ? artifactLabel(inspected.name) : "Visualização da drenagem"}</h2></div>
          <nav className="workspace-tabs" aria-label="Abas de visualização">
            <button className={workspaceTab === "map" ? "active" : ""} type="button" onClick={() => setWorkspaceTab("map")}><Map size={14} /> Mapa</button>
            <button className={workspaceTab === "files" ? "active" : ""} type="button" onClick={openFileManager}><FileBox size={14} /> Arquivos</button>
          </nav>
        </header>
        <div className="map-frame">
          {workspaceTab === "files" ? <div className="file-manager">
            <div className="file-manager-heading">
              <div><span className="workspace-kicker">Dados gerados</span><h3>Arquivos de saída</h3><p>{outputFiles.length} {outputFiles.length === 1 ? "arquivo no diretório de saída" : "arquivos no diretório de saída"}</p></div>
              <div className="file-manager-actions">
                <button className="select-all-files" type="button" onClick={toggleAllFiles} disabled={!outputFiles.length}>{allFilesSelected ? "Limpar seleção" : "Selecionar todos"}</button>
                {selectedFiles.length > 0 && <button className="bulk-delete" type="button" onClick={deleteSelectedFiles} disabled={deletingFile === "bulk"}><Trash2 size={14} /> Excluir arquivos ({selectedFiles.length})</button>}
                <button className="refresh-files" type="button" onClick={refreshOutputFiles} disabled={filesLoading || deletingFile === "bulk"} title="Atualizar arquivos"><RefreshCw className={filesLoading ? "spin" : ""} size={16} /></button>
              </div>
            </div>
            <label className="file-search"><Search size={15} /><input value={fileSearch} onChange={(event) => setFileSearch(event.target.value)} placeholder="Buscar por processamento ou nome do arquivo" /></label>
            {filesError && <div className="file-manager-state error"><TriangleAlert size={18} /> {filesError}</div>}
            {filesLoading && <div className="file-manager-state"><LoaderCircle className="spin" size={22} /> Carregando arquivos de saída…</div>}
            {!filesLoading && !filesError && !visibleOutputFiles.length && <div className="file-manager-state"><FileBox size={24} /><strong>{outputFiles.length ? "Nenhum arquivo encontrado para esta busca" : "Ainda não há arquivos de saída"}</strong><span>Execute um processamento para gerar arquivos neste diretório.</span></div>}
            {!filesLoading && !filesError && visibleOutputFiles.length > 0 && <div className="file-table">
              <div className="file-table-head"><span /><span>Arquivo</span><span>Processamento</span><span>Tamanho</span><span>Alterado</span><span /></div>
              {visibleOutputFiles.map((file) => <div className="file-row" key={`${file.job_id}/${file.name}`}>
                <input className="file-check" type="checkbox" checked={selectedFiles.includes(`${file.job_id}/${file.name}`)} onChange={() => toggleFile(file)} aria-label={`Selecionar ${file.name}`} />
                <span className="file-name"><FileBox size={17} /><strong>{file.name}</strong><small>{file.kind === "vector" ? "GeoParquet" : file.kind === "raster" ? "GeoTIFF" : "Metadados"}</small></span>
                <code>{file.job_id}</code>
                <span>{formatBytes(file.size)}</span>
                <span>{fileDate(file.modified_at)}</span>
                <span className="file-actions"><a href={`${API_BASE}${file.download_url}`} download={file.name} title={`Baixar ${file.name}`}><ArrowDownToLine size={16} /></a><button type="button" onClick={() => deleteOutputFile(file)} disabled={deletingFile === `${file.job_id}/${file.name}`} title={`Excluir ${file.name}`}><Trash2 size={16} /></button></span>
              </div>)}
            </div>}
          </div> : job?.status === "complete" && inspected ? <MapViewer key={`${job.id}:${inspected.name}`} apiBase={API_BASE} jobId={job.id} artifact={inspected} /> : <div className="map-empty">
            <div className="contour contour-one" /><div className="contour contour-two" /><div className="contour contour-three" />
            <span className="map-empty-icon"><Map size={28} /></span>
            <h3>{job?.status === "complete" ? "Selecione uma camada para visualizar" : "O mapa aparecerá aqui"}</h3>
            <p>{job?.status === "complete" ? "Escolha um resultado e clique em Visualizar. As camadas não são abertas automaticamente." : "Selecione os arquivos DEM e gere a rede de drenagem para visualizar a hierarquia extraída."}</p>
            <div className="order-preview"><span><i className="order-1" /> Drenagem principal · ordem 1</span><span><i className="order-2" /> Tributários diretos · ordem 2</span><span><i className="order-3" /> Ramificações superiores · ordem 3+</span></div>
          </div>}
        </div>
        <footer className="workspace-footer"><span>Ordem Hack: quanto menor o número, mais próximo do canal principal</span><span>WhiteboxTools · modelo de fluxo D8</span></footer>
      </section>
    </main>
  );
}
