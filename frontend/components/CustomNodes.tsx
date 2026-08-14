"use client";

/**
 * CanvasFlow node cards.
 *
 * Every node type is the same shell — `NodeShell` — differing only in accent colour,
 * icon, handle topology, and which fields it exposes. That keeps the visual language
 * identical across the canvas and means a new node type is ~10 lines at the bottom
 * of this file.
 */

import { Handle, Position, useReactFlow, type NodeProps } from "@xyflow/react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  FileInput,
  FileText,
  FlaskConical,
  Loader2,
  Megaphone,
  MinusCircle,
  PenLine,
  Play,
  RotateCcw,
  Sparkles,
  Trash2,
  UploadCloud,
  Video,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { memo, useCallback, useRef, useState } from "react";

import { deleteSource, ingestFile, ingestUrl } from "@/lib/api";
import {
  NODE_PERSONAS,
  SOURCE_KIND_LABELS,
  type CanvasNodeData,
  type CanvasNodeType,
  type IngestedSource,
  type NodeRunStatus,
} from "@/types";

/* --------------------------------------------------------------- accent palette */

type Accent = "indigo" | "violet" | "cyan" | "emerald" | "amber" | "fuchsia" | "sky";

/** Tailwind can't build class names at runtime, so every variant is spelled out. */
const ACCENTS: Record<Accent, { ring: string; text: string; chip: string; glow: string }> = {
  indigo: {
    ring: "border-indigo-500/25",
    text: "text-indigo-300",
    chip: "bg-indigo-500/10 text-indigo-200 ring-1 ring-inset ring-indigo-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(99_102_241/0.85)]",
  },
  violet: {
    ring: "border-violet-500/25",
    text: "text-violet-300",
    chip: "bg-violet-500/10 text-violet-200 ring-1 ring-inset ring-violet-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(139_92_246/0.85)]",
  },
  cyan: {
    ring: "border-cyan-500/25",
    text: "text-cyan-300",
    chip: "bg-cyan-500/10 text-cyan-200 ring-1 ring-inset ring-cyan-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(34_211_238/0.85)]",
  },
  emerald: {
    ring: "border-emerald-500/25",
    text: "text-emerald-300",
    chip: "bg-emerald-500/10 text-emerald-200 ring-1 ring-inset ring-emerald-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(16_185_129/0.85)]",
  },
  amber: {
    ring: "border-amber-500/25",
    text: "text-amber-300",
    chip: "bg-amber-500/10 text-amber-200 ring-1 ring-inset ring-amber-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(245_158_11/0.85)]",
  },
  sky: {
    ring: "border-sky-500/25",
    text: "text-sky-300",
    chip: "bg-sky-500/10 text-sky-200 ring-1 ring-inset ring-sky-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(14_165_233/0.85)]",
  },
  fuchsia: {
    ring: "border-fuchsia-500/25",
    text: "text-fuchsia-300",
    chip: "bg-fuchsia-500/10 text-fuchsia-200 ring-1 ring-inset ring-fuchsia-400/25",
    glow: "shadow-[0_0_40px_-16px_rgb(217_70_239/0.85)]",
  },
};

/* ------------------------------------------------------------------ status pill */

const STATUS_STYLES: Record<
  Exclude<NodeRunStatus, "idle">,
  { icon: LucideIcon; label: string; className: string; spin?: boolean }
> = {
  running: {
    icon: Loader2,
    label: "Running",
    className: "bg-indigo-500/15 text-indigo-200",
    spin: true,
  },
  ok: { icon: CheckCircle2, label: "Done", className: "bg-emerald-500/15 text-emerald-300" },
  error: { icon: AlertTriangle, label: "Failed", className: "bg-rose-500/15 text-rose-300" },
  skipped: { icon: MinusCircle, label: "Skipped", className: "bg-slate-500/15 text-slate-400" },
};

function StatusPill({ status, durationMs }: { status?: NodeRunStatus; durationMs?: number }) {
  if (!status || status === "idle") return null;
  const style = STATUS_STYLES[status];
  const Icon = style.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${style.className}`}
    >
      <Icon size={11} className={style.spin ? "animate-spin" : undefined} />
      {style.label}
      {durationMs != null && status !== "running" ? (
        <span className="opacity-60">{(durationMs / 1000).toFixed(1)}s</span>
      ) : null}
    </span>
  );
}

/* --------------------------------------------------------------- field controls */

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-slate-500">
      {children}
    </span>
  );
}

/** `nodrag`/`nowheel` stop React Flow from panning the canvas while typing or scrolling. */
function TextArea({
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  rows?: number;
}) {
  return (
    <textarea
      className="field nodrag nowheel resize-none"
      rows={rows}
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function PersonaSelect({
  value,
  options,
  onChange,
  accent,
}: {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  accent: Accent;
}) {
  return (
    <div className="relative">
      <select
        className={`field nodrag cursor-pointer appearance-none pr-8 ${ACCENTS[accent].text}`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">No persona (node default)</option>
        {options.map((option) => (
          <option key={option} value={option} className="bg-obsidian-900 text-slate-200">
            {option}
          </option>
        ))}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-500"
      />
    </div>
  );
}

/* -------------------------------------------------------------------- node shell */

interface ShellConfig {
  icon: LucideIcon;
  kind: string;
  accent: Accent;
  /** Placeholder for the instruction field. */
  promptPlaceholder: string;
  /** Source-material field, for node types that take pasted input. */
  contentPlaceholder?: string;
  hasInput: boolean;
  hasOutput: boolean;
}

/**
 * `onRunNode` is injected once by Canvas via React Flow's `nodeTypes` closure —
 * see `createNodeTypes` at the bottom of this file.
 */
export type RunNodeHandler = (nodeId: string) => void;

function NodeShell({
  id,
  data,
  selected,
  config,
  onRunNode,
}: NodeProps & {
  data: CanvasNodeData;
  config: ShellConfig;
  onRunNode?: RunNodeHandler;
}) {
  const { updateNodeData, deleteElements } = useReactFlow();
  const [outputOpen, setOutputOpen] = useState(true);
  const accent = ACCENTS[config.accent];
  const Icon = config.icon;
  const personas = NODE_PERSONAS[config.kind as CanvasNodeType] ?? [];
  const isRunning = data.status === "running";

  const patch = useCallback(
    (fields: Partial<CanvasNodeData>) => updateNodeData(id, fields),
    [id, updateNodeData],
  );

  return (
    <div
      className={`cf-node w-[320px] rounded-2xl border ${accent.ring} bg-obsidian-800/70 backdrop-blur-md
                  transition-all duration-200 ${accent.glow}
                  ${selected ? "ring-1 ring-indigo-400/40" : ""}
                  ${isRunning ? "animate-pulse-ring" : ""}`}
    >
      {config.hasInput ? <Handle type="target" position={Position.Left} /> : null}

      {/* Header ------------------------------------------------------------- */}
      <header className="flex items-start gap-2.5 border-b border-white/5 px-4 py-3">
        <span className={`mt-0.5 rounded-lg p-1.5 ${accent.chip}`}>
          <Icon size={14} />
        </span>

        <div className="min-w-0 flex-1">
          <input
            className="nodrag w-full bg-transparent text-sm font-semibold text-slate-100
                       outline-none placeholder:text-slate-600"
            value={data.label}
            placeholder="Untitled node"
            onChange={(event) => patch({ label: event.target.value })}
          />
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className={`text-[10px] uppercase tracking-widest ${accent.text}`}>
              {config.kind}
            </span>
            <StatusPill status={data.status} durationMs={data.durationMs} />
          </div>
        </div>

        <button
          type="button"
          title="Run this node"
          disabled={isRunning}
          onClick={() => onRunNode?.(id)}
          className="nodrag rounded-md p-1.5 text-slate-400 transition
                     hover:bg-indigo-500/15 hover:text-indigo-200 disabled:opacity-40"
        >
          {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
        </button>
        <button
          type="button"
          title="Delete node"
          onClick={() => deleteElements({ nodes: [{ id }] })}
          className="nodrag rounded-md p-1.5 text-slate-500 transition
                     hover:bg-rose-500/15 hover:text-rose-300"
        >
          <Trash2 size={14} />
        </button>
      </header>

      {/* Body --------------------------------------------------------------- */}
      <div className="space-y-3 px-4 py-3">
        {personas.length > 0 ? (
          <div>
            <Label>Persona</Label>
            <PersonaSelect
              value={data.persona ?? ""}
              options={personas}
              accent={config.accent}
              onChange={(persona) => patch({ persona })}
            />
          </div>
        ) : null}

        {config.contentPlaceholder ? (
          <div>
            <Label>Source material</Label>
            <TextArea
              value={data.content ?? ""}
              placeholder={config.contentPlaceholder}
              onChange={(content) => patch({ content })}
              rows={3}
            />
          </div>
        ) : null}

        <div>
          <Label>Instruction</Label>
          <TextArea
            value={data.prompt}
            placeholder={config.promptPlaceholder}
            onChange={(prompt) => patch({ prompt })}
            rows={3}
          />
        </div>

        {data.error ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-200">
            {data.error}
          </p>
        ) : null}

        {data.output ? (
          <div className="rounded-lg border border-white/5 bg-obsidian-950/60">
            <button
              type="button"
              onClick={() => setOutputOpen((open) => !open)}
              className="nodrag flex w-full items-center justify-between px-3 py-2 text-[10px]
                         font-semibold uppercase tracking-widest text-slate-400 hover:text-slate-200"
            >
              <span className="inline-flex items-center gap-1.5">
                <Sparkles size={11} className={accent.text} />
                Output
              </span>
              <ChevronDown
                size={13}
                className={`transition-transform ${outputOpen ? "" : "-rotate-90"}`}
              />
            </button>
            {outputOpen ? (
              <pre className="nowheel max-h-52 overflow-auto whitespace-pre-wrap px-3 pb-3
                              text-xs leading-relaxed text-slate-300">
                {data.output}
              </pre>
            ) : null}
          </div>
        ) : null}
      </div>

      {config.hasOutput ? <Handle type="source" position={Position.Right} /> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ source node */

const ACCEPTED_FILES = ".pdf,.docx,.txt,.md,.csv,.mp3,.mp4,.wav,.m4a,.mov,.webm";

/** Ingestion of an A/V file runs Whisper locally, so the wait is minutes, not seconds. */
const INGEST_MESSAGES = {
  link: "Fetching and extracting text…",
  file: "Transcription and extraction run locally on your machine — audio and video can take several minutes.",
} as const;

// Split rather than joined into a string so the numbers can carry their own weight —
// scale is the thing users misread when they only see a 1,200-char preview.
function sourceStats(source: IngestedSource): { value: string; unit?: string }[] {
  const stats: { value: string; unit?: string }[] = [
    { value: source.char_count.toLocaleString(), unit: "chars" },
  ];
  if (source.meta.duration_label) stats.push({ value: source.meta.duration_label });
  if (source.meta.pages) stats.push({ value: String(source.meta.pages), unit: "pages" });
  if (source.meta.site) stats.push({ value: source.meta.site });
  return stats;
}

/** Only offered above this size; below it every strategy reads the whole source anyway. */
const COVERAGE_THRESHOLD = 20_000;

const COVERAGE_OPTIONS = [
  {
    value: "auto",
    label: "Auto",
    description: "Picks whole-source when it fits, retrieval when it doesn't.",
  },
  {
    value: "retrieve",
    label: "Relevant",
    description: "Fastest; sends only passages matching this node's instruction.",
  },
  {
    value: "full",
    label: "Complete",
    description:
      "Reads the entire source in sequential passes, then synthesises; slowest and costs several model calls.",
  },
] as const;

function SourceNodeCard({ id, data, selected }: NodeProps & { data: CanvasNodeData }) {
  const { updateNodeData, deleteElements } = useReactFlow();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"link" | "file">("link");
  const [dragOver, setDragOver] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const accent = ACCENTS.sky;

  const patch = useCallback(
    (fields: Partial<CanvasNodeData>) => updateNodeData(id, fields),
    [id, updateNodeData],
  );

  const run = useCallback(
    async (ingest: () => Promise<IngestedSource>) => {
      patch({ ingesting: true, error: undefined });
      try {
        patch({ source: await ingest(), ingesting: false });
      } catch (error) {
        patch({
          ingesting: false,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [patch],
  );

  const replace = useCallback(() => {
    const previous = data.source;
    // The node moves on regardless; an orphaned server-side source is not worth blocking on.
    if (previous) void deleteSource(previous.source_id).catch(() => undefined);
    patch({ source: undefined, url: "", error: undefined });
    setPreviewOpen(false);
  }, [data.source, patch]);

  const source = data.source;
  const url = data.url ?? "";
  const coverage = data.coverage ?? "auto";

  return (
    <div
      className={`cf-node w-[320px] rounded-2xl border ${accent.ring} bg-obsidian-800/70 backdrop-blur-md
                  transition-all duration-200 ${accent.glow}
                  ${selected ? "ring-1 ring-indigo-400/40" : ""}
                  ${data.ingesting ? "animate-pulse-ring" : ""}`}
    >
      {/* Header ------------------------------------------------------------- */}
      <header className="flex items-start gap-2.5 border-b border-white/5 px-4 py-3">
        <span className={`mt-0.5 rounded-lg p-1.5 ${accent.chip}`}>
          <FileInput size={14} />
        </span>

        <div className="min-w-0 flex-1">
          <input
            className="nodrag w-full bg-transparent text-sm font-semibold text-slate-100
                       outline-none placeholder:text-slate-600"
            value={data.label}
            placeholder="Untitled source"
            onChange={(event) => patch({ label: event.target.value })}
          />
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className={`text-[10px] uppercase tracking-widest ${accent.text}`}>
              {source ? SOURCE_KIND_LABELS[source.kind] : "source"}
            </span>
            <StatusPill status={data.status} durationMs={data.durationMs} />
          </div>
        </div>

        <button
          type="button"
          title="Delete node"
          onClick={() => deleteElements({ nodes: [{ id }] })}
          className="nodrag rounded-md p-1.5 text-slate-500 transition
                     hover:bg-rose-500/15 hover:text-rose-300"
        >
          <Trash2 size={14} />
        </button>
      </header>

      {/* Body --------------------------------------------------------------- */}
      <div className="space-y-3 px-4 py-3">
        {data.ingesting ? (
          <div className="flex items-start gap-2.5 rounded-lg border border-sky-500/20 bg-obsidian-950/60 px-3 py-3">
            <Loader2 size={14} className={`mt-0.5 shrink-0 animate-spin ${accent.text}`} />
            <p className="text-xs leading-relaxed text-slate-400">{INGEST_MESSAGES[mode]}</p>
          </div>
        ) : source ? (
          <div className="rounded-lg border border-white/5 bg-obsidian-950/60">
            <div className="flex items-start gap-2 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-slate-200">{source.title}</p>
                <div className="mt-1 flex flex-wrap items-baseline gap-x-1.5 text-[10px] text-slate-500">
                  {sourceStats(source).map((stat, index) => (
                    <span key={stat.value} className="inline-flex items-baseline gap-1">
                      {index > 0 ? <span className="pr-0.5 text-slate-700">·</span> : null}
                      <span className="text-xs font-semibold tabular-nums text-slate-100">
                        {stat.value}
                      </span>
                      {stat.unit}
                    </span>
                  ))}
                </div>
                <p className="mt-1 truncate text-[10px] text-slate-500">{source.origin}</p>
              </div>
              <button
                type="button"
                title="Replace this source"
                onClick={replace}
                className="nodrag rounded-md p-1.5 text-slate-500 transition
                           hover:bg-sky-500/15 hover:text-sky-200"
              >
                <RotateCcw size={13} />
              </button>
            </div>

            {source.preview ? (
              <>
                <button
                  type="button"
                  onClick={() => setPreviewOpen((open) => !open)}
                  className="nodrag flex w-full items-center justify-between border-t border-white/5 px-3 py-2
                             text-[10px] font-semibold uppercase tracking-widest text-slate-400 hover:text-slate-200"
                >
                  <span className="inline-flex items-center gap-1.5 text-left">
                    <Sparkles size={11} className={`shrink-0 ${accent.text}`} />
                    Preview — first {source.preview.length.toLocaleString()} characters of{" "}
                    {source.char_count.toLocaleString()}
                  </span>
                  <ChevronDown
                    size={13}
                    className={`shrink-0 transition-transform ${previewOpen ? "" : "-rotate-90"}`}
                  />
                </button>
                {previewOpen ? (
                  <>
                    <pre className="nowheel max-h-52 overflow-auto whitespace-pre-wrap px-3 pb-2
                                    text-xs leading-relaxed text-slate-300">
                      {source.preview}
                    </pre>
                    {source.preview.length < source.char_count ? (
                      <p className="px-3 pb-3 text-[10px] leading-relaxed text-slate-500">
                        Truncated for display only. The full{" "}
                        {source.char_count.toLocaleString()} characters are stored on the backend
                        and reach the AI at run time.
                      </p>
                    ) : null}
                  </>
                ) : null}
              </>
            ) : null}

            {source.char_count > COVERAGE_THRESHOLD ? (
              <div className="border-t border-white/5 px-3 py-2.5">
                <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
                  Coverage
                </p>
                <div className="flex gap-1 rounded-lg border border-indigo-500/20 bg-obsidian-950/70 p-1">
                  {COVERAGE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      title={option.description}
                      onClick={() => patch({ coverage: option.value })}
                      className={`nodrag flex-1 rounded-md px-2 py-1 text-[11px] font-medium transition
                                  ${
                                    coverage === option.value
                                      ? "bg-sky-500/15 text-sky-200"
                                      : "text-slate-400 hover:text-slate-200"
                                  }`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-[10px] leading-relaxed text-slate-500">
                  {COVERAGE_OPTIONS.find((option) => option.value === coverage)?.description}
                </p>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            <div className="flex gap-1 rounded-lg border border-indigo-500/20 bg-obsidian-950/70 p-1">
              {(["link", "file"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setMode(option)}
                  className={`nodrag flex-1 rounded-md px-2 py-1 text-[11px] font-medium capitalize transition
                              ${
                                mode === option
                                  ? "bg-sky-500/15 text-sky-200"
                                  : "text-slate-400 hover:text-slate-200"
                              }`}
                >
                  {option}
                </button>
              ))}
            </div>

            {mode === "link" ? (
              <div className="flex gap-2">
                <input
                  className="field nodrag flex-1"
                  value={url}
                  placeholder="https://youtube.com/watch?v=…"
                  onChange={(event) => patch({ url: event.target.value })}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && url.trim()) void run(() => ingestUrl(url.trim()));
                  }}
                />
                <button
                  type="button"
                  disabled={!url.trim()}
                  onClick={() => void run(() => ingestUrl(url.trim()))}
                  className="btn-ghost nodrag shrink-0"
                >
                  Ingest
                </button>
              </div>
            ) : (
              <div
                role="button"
                tabIndex={0}
                onClick={() => fileInput.current?.click()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") fileInput.current?.click();
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragOver(false);
                  const file = event.dataTransfer.files[0];
                  if (file) void run(() => ingestFile(file));
                }}
                className={`nodrag flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border
                            border-dashed px-3 py-5 text-center transition
                            ${
                              dragOver
                                ? "border-sky-400/60 bg-sky-500/10"
                                : "border-indigo-500/25 bg-obsidian-950/70 hover:border-sky-400/40"
                            }`}
              >
                <UploadCloud size={18} className={accent.text} />
                <p className="text-xs text-slate-400">Drop a file, or click to browse</p>
                <p className="text-[10px] text-slate-600">PDF, DOCX, TXT, MD, CSV, audio, video</p>
                <input
                  ref={fileInput}
                  type="file"
                  className="hidden"
                  accept={ACCEPTED_FILES}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    // Reset so re-picking the same file still fires a change event.
                    event.target.value = "";
                    if (file) void run(() => ingestFile(file));
                  }}
                />
              </div>
            )}
          </>
        )}

        {data.error ? (
          <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs leading-relaxed text-rose-200">
            {data.error}
          </p>
        ) : null}

        {data.retrieval ? (
          <p
            className={`rounded-lg px-3 py-2 text-[10px] leading-relaxed ${
              data.retrieval.strategy === "whole"
                ? "bg-slate-500/10 text-slate-400"
                : "bg-amber-500/10 text-amber-200"
            }`}
          >
            {data.retrieval.note}
          </p>
        ) : null}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

/* ------------------------------------------------------------------ node types */

// `sourceNode` is absent: it ingests rather than generates, so it has its own card.
const NODE_CONFIGS: Record<Exclude<CanvasNodeType, "sourceNode">, ShellConfig> = {
  campaignInput: {
    icon: Megaphone,
    kind: "campaignInput",
    accent: "amber",
    hasInput: false,
    hasOutput: true,
    contentPlaceholder: "Paste the raw brief, product notes, or research…",
    promptPlaceholder: "Turn this into a structured campaign brief.",
  },
  copywriting: {
    icon: PenLine,
    kind: "copywriting",
    accent: "indigo",
    hasInput: true,
    hasOutput: true,
    promptPlaceholder: "Write three ad variants under 120 characters each.",
  },
  videoScript: {
    icon: Video,
    kind: "videoScript",
    accent: "violet",
    hasInput: true,
    hasOutput: true,
    promptPlaceholder: "Write a 30-second vertical script with a 2-second hook.",
  },
  researchNode: {
    icon: FlaskConical,
    kind: "researchNode",
    accent: "cyan",
    hasInput: true,
    hasOutput: true,
    contentPlaceholder: "Optional: paste source material to analyse…",
    promptPlaceholder: "Pull out the three claims worth testing.",
  },
  transformNode: {
    icon: Wand2,
    kind: "transformNode",
    accent: "fuchsia",
    hasInput: true,
    hasOutput: true,
    promptPlaceholder: "Rewrite the input for LinkedIn, keeping the numbers intact.",
  },
  output: {
    icon: FileText,
    kind: "output",
    accent: "emerald",
    hasInput: true,
    hasOutput: false,
    promptPlaceholder: "Assemble the inputs into the final deliverable.",
  },
};

/**
 * Builds the `nodeTypes` map React Flow expects.
 *
 * Canvas memoizes the result — a fresh object each render would remount every node
 * and blow away focus mid-keystroke.
 */
export function createNodeTypes(onRunNode: RunNodeHandler) {
  const SourceNode = memo((props: NodeProps) => (
    <SourceNodeCard {...props} data={props.data as CanvasNodeData} />
  ));
  SourceNode.displayName = "sourceNodeNode";

  const entries = (Object.keys(NODE_CONFIGS) as Exclude<CanvasNodeType, "sourceNode">[]).map((kind) => {
    const config = NODE_CONFIGS[kind];
    const Component = memo((props: NodeProps) => (
      <NodeShell
        {...props}
        data={props.data as CanvasNodeData}
        config={config}
        onRunNode={onRunNode}
      />
    ));
    Component.displayName = `${kind}Node`;
    return [kind, Component] as const;
  });
  return { ...Object.fromEntries(entries), sourceNode: SourceNode };
}

/** Palette metadata for the "add node" menu in the control bar. */
export const NODE_CATALOG: readonly {
  type: CanvasNodeType;
  label: string;
  icon: LucideIcon;
  accent: Accent;
}[] = [
  { type: "sourceNode", label: "Source", icon: FileInput, accent: "sky" },
  { type: "campaignInput", label: "Campaign Input", icon: Megaphone, accent: "amber" },
  { type: "researchNode", label: "Research", icon: FlaskConical, accent: "cyan" },
  { type: "copywriting", label: "Copywriting", icon: PenLine, accent: "indigo" },
  { type: "videoScript", label: "Video Script", icon: Video, accent: "violet" },
  { type: "transformNode", label: "Transform", icon: Wand2, accent: "fuchsia" },
  { type: "output", label: "Output", icon: FileText, accent: "emerald" },
] as const;

export const ACCENT_CHIP = (accent: Accent) => ACCENTS[accent].chip;

/** Default data for a freshly dropped node. */
export function defaultNodeData(type: CanvasNodeType): CanvasNodeData {
  const entry = NODE_CATALOG.find((item) => item.type === type);
  return {
    label: entry?.label ?? "Node",
    prompt: "",
    persona: "",
    content: "",
    status: "idle",
    ...(type === "sourceNode" ? { url: "" } : {}),
  };
}
