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
  FileText,
  FlaskConical,
  Loader2,
  Megaphone,
  MinusCircle,
  PenLine,
  Play,
  Sparkles,
  Trash2,
  Video,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { memo, useCallback, useState } from "react";

import {
  NODE_PERSONAS,
  type CanvasNodeData,
  type CanvasNodeType,
  type NodeRunStatus,
} from "@/types";

/* --------------------------------------------------------------- accent palette */

type Accent = "indigo" | "violet" | "cyan" | "emerald" | "amber" | "fuchsia";

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

/* ------------------------------------------------------------------ node types */

const NODE_CONFIGS: Record<CanvasNodeType, ShellConfig> = {
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
  const entries = (Object.keys(NODE_CONFIGS) as CanvasNodeType[]).map((kind) => {
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
  return Object.fromEntries(entries);
}

/** Palette metadata for the "add node" menu in the control bar. */
export const NODE_CATALOG: readonly {
  type: CanvasNodeType;
  label: string;
  icon: LucideIcon;
  accent: Accent;
}[] = [
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
  };
}
