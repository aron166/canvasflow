"use client";

/**
 * The CanvasFlow workspace.
 *
 * Owns the graph, the engine config, autosave, and the run loop. Node cards render
 * themselves (CustomNodes.tsx); this file is the orchestration layer around them.
 */

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type EdgeTypes,
  type OnConnect,
} from "@xyflow/react";
import {
  Check,
  Download,
  KeyRound,
  Loader2,
  Play,
  Plus,
  Settings2,
  Terminal,
  Trash2,
  TriangleAlert,
  Upload,
  HardDrive,
  type LucideIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ACCENT_CHIP,
  NODE_CATALOG,
  createNodeTypes,
  defaultNodeData,
} from "@/components/CustomNodes";
import SettingsModal from "@/components/SettingsModal";
import { ApiError, executeNode, executeWorkflow } from "@/lib/api";
import {
  buildDocument,
  exportWorkflow,
  loadEngine,
  loadWorkflow,
  parseWorkflow,
  saveEngine,
  saveWorkflow,
} from "@/lib/storage";
import {
  DEFAULT_ENGINE_CONFIG,
  ENGINE_DESCRIPTORS,
  type CanvasEdge,
  type CanvasNode,
  type CanvasNodeData,
  type CanvasNodeType,
  type EngineConfig,
  type EngineProvider,
} from "@/types";

const PROVIDER_ICONS: Record<EngineProvider, LucideIcon> = {
  anthropic_api: KeyRound,
  claude_cli: Terminal,
  ollama: HardDrive,
};

/* ---------------------------------------------------------------- starter graph */

/** A two-node example so the canvas is never a blank void on first load. */
function starterGraph(): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  return {
    nodes: [
      {
        id: "brief",
        type: "campaignInput",
        position: { x: 40, y: 140 },
        data: {
          ...defaultNodeData("campaignInput"),
          label: "Campaign Brief",
          content:
            "Product: a refillable steel water bottle.\nAudience: commuters, 25-40.\nBudget: modest. Launch in three weeks.",
          prompt: "Turn this into a structured campaign brief.",
        },
      },
      {
        id: "copy",
        type: "copywriting",
        position: { x: 440, y: 160 },
        data: {
          ...defaultNodeData("copywriting"),
          label: "Launch Copy",
          persona: "Dry and deadpan",
          prompt: "Write three ad variants, each under 120 characters.",
        },
      },
    ],
    edges: [
      { id: "brief-copy", source: "brief", target: "copy", animated: true },
    ],
  };
}

/* ------------------------------------------------------------------- edge paint */

/** Defines the gradient that globals.css points every edge stroke at. */
function EdgeGradient() {
  return (
    <svg className="pointer-events-none absolute h-0 w-0" aria-hidden>
      <defs>
        <linearGradient id="cf-edge-gradient" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#818cf8" />
          <stop offset="100%" stopColor="#c084fc" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/* -------------------------------------------------------------------- add menu */

function AddNodeMenu({ onAdd }: { onAdd: (type: CanvasNodeType) => void }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button type="button" className="btn-ghost" onClick={() => setOpen((v) => !v)}>
        <Plus size={14} /> Add node
      </button>

      {open ? (
        <>
          {/* Click-away layer — cheaper and more reliable than a document listener. */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="glass absolute bottom-full z-20 mb-2 w-56 animate-fade-up
                          overflow-hidden rounded-xl p-1.5 shadow-glow">
            {NODE_CATALOG.map(({ type, label, icon: Icon, accent }) => (
              <button
                key={type}
                type="button"
                onClick={() => {
                  onAdd(type);
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left
                           text-sm text-slate-300 transition hover:bg-indigo-500/10 hover:text-white"
              >
                <span className={`rounded-md p-1.5 ${ACCENT_CHIP(accent)}`}>
                  <Icon size={13} />
                </span>
                {label}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------- run summary */

type RunSummary =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; ok: number; failed: number; ms: number }
  | { kind: "error"; message: string };

function RunBanner({ summary }: { summary: RunSummary }) {
  if (summary.kind === "idle") return null;

  if (summary.kind === "error") {
    return (
      <div className="glass flex max-w-md items-start gap-2 rounded-xl px-3 py-2 text-xs text-rose-200">
        <TriangleAlert size={14} className="mt-0.5 shrink-0" />
        <span className="leading-relaxed">{summary.message}</span>
      </div>
    );
  }

  if (summary.kind === "running") {
    return (
      <div className="glass flex items-center gap-2 rounded-xl px-3 py-2 text-xs text-indigo-200">
        <Loader2 size={14} className="animate-spin" /> Executing workflow…
      </div>
    );
  }

  const failed = summary.failed > 0;
  return (
    <div
      className={`glass flex items-center gap-2 rounded-xl px-3 py-2 text-xs ${
        failed ? "text-amber-200" : "text-emerald-200"
      }`}
    >
      {failed ? <TriangleAlert size={14} /> : <Check size={14} />}
      {summary.ok} succeeded
      {failed ? `, ${summary.failed} failed` : ""} · {(summary.ms / 1000).toFixed(1)}s
    </div>
  );
}

/* ---------------------------------------------------------------------- canvas */

function CanvasInner() {
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<CanvasEdge>([]);
  const [engine, setEngine] = useState<EngineConfig>(DEFAULT_ENGINE_CONFIG);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [summary, setSummary] = useState<RunSummary>({ kind: "idle" });
  const [name, setName] = useState("Untitled workflow");

  const { screenToFlowPosition, getNodes, getEdges } = useReactFlow<CanvasNode, CanvasEdge>();
  const fileInput = useRef<HTMLInputElement>(null);
  const hydrated = useRef(false);

  /* -- hydrate from LocalStorage (client only — the server has no storage) ----- */
  useEffect(() => {
    const saved = loadWorkflow();
    const graph = saved ?? starterGraph();
    setNodes(graph.nodes);
    setEdges(graph.edges);
    if (saved) setName(saved.name);
    setEngine(loadEngine());
    hydrated.current = true;
  }, [setNodes, setEdges]);

  /* -- autosave, debounced so a keystroke isn't a write ----------------------- */
  useEffect(() => {
    if (!hydrated.current) return;
    const timer = setTimeout(() => saveWorkflow(name, nodes, edges), 500);
    return () => clearTimeout(timer);
  }, [name, nodes, edges]);

  const updateEngine = useCallback((next: EngineConfig) => {
    setEngine(next);
    saveEngine(next);
  }, []);

  /* -- graph edits ------------------------------------------------------------ */

  const onConnect: OnConnect = useCallback(
    (connection: Connection) =>
      setEdges((current) => addEdge({ ...connection, animated: true }, current)),
    [setEdges],
  );

  const patchNode = useCallback(
    (id: string, fields: Partial<CanvasNodeData>) =>
      setNodes((current) =>
        current.map((node) =>
          node.id === id ? { ...node, data: { ...node.data, ...fields } } : node,
        ),
      ),
    [setNodes],
  );

  const addNode = useCallback(
    (type: CanvasNodeType) => {
      // Drop new nodes near the middle of what the user is currently looking at.
      const position = screenToFlowPosition({
        x: window.innerWidth / 2 + (Math.random() - 0.5) * 160,
        y: window.innerHeight / 2 + (Math.random() - 0.5) * 160,
      });
      setNodes((current) => [
        ...current,
        {
          id: `${type}-${Date.now().toString(36)}`,
          type,
          position,
          data: defaultNodeData(type),
        },
      ]);
    },
    [screenToFlowPosition, setNodes],
  );

  /* -- execution -------------------------------------------------------------- */

  /** Run a single node with whatever its parents last produced. */
  const runSingleNode = useCallback(
    async (nodeId: string) => {
      const node = getNodes().find((candidate) => candidate.id === nodeId);
      if (!node) return;

      const upstream = getEdges()
        .filter((edge) => edge.target === nodeId)
        .map((edge) => getNodes().find((candidate) => candidate.id === edge.source))
        .map((parent) => parent?.data.output ?? "")
        .filter(Boolean);

      patchNode(nodeId, { status: "running", error: undefined });
      try {
        const result = await executeNode(node, upstream, engine);
        patchNode(nodeId, {
          status: result.status,
          output: result.output,
          error: result.error ?? undefined,
          durationMs: result.duration_ms,
        });
      } catch (error) {
        patchNode(nodeId, {
          status: "error",
          error: error instanceof ApiError ? error.message : String(error),
        });
      }
    },
    [getNodes, getEdges, engine, patchNode],
  );

  /** Serialize the graph and hand the whole thing to the backend executor. */
  const runWorkflow = useCallback(async () => {
    const currentNodes = getNodes();
    if (currentNodes.length === 0) return;

    setRunning(true);
    setSummary({ kind: "running" });
    setNodes((current) =>
      current.map((node) => ({
        ...node,
        data: { ...node.data, status: "running", error: undefined },
      })),
    );

    try {
      const response = await executeWorkflow(currentNodes, getEdges(), engine);
      const byId = new Map(response.results.map((result) => [result.node_id, result]));

      setNodes((current) =>
        current.map((node) => {
          const result = byId.get(node.id);
          if (!result) return { ...node, data: { ...node.data, status: "idle" as const } };
          return {
            ...node,
            data: {
              ...node.data,
              status: result.status,
              output: result.output || node.data.output,
              error: result.error ?? undefined,
              durationMs: result.duration_ms,
            },
          };
        }),
      );

      setSummary({
        kind: "done",
        ok: response.results.filter((result) => result.status === "ok").length,
        failed: response.results.filter((result) => result.status !== "ok").length,
        ms: response.duration_ms,
      });
    } catch (error) {
      // A transport-level failure means no node ran — clear the running state rather
      // than leaving every card spinning forever.
      setNodes((current) =>
        current.map((node) => ({ ...node, data: { ...node.data, status: "idle" as const } })),
      );
      setSummary({
        kind: "error",
        message: error instanceof ApiError ? error.message : String(error),
      });
    } finally {
      setRunning(false);
    }
  }, [getNodes, getEdges, engine, setNodes]);

  /* -- import / export -------------------------------------------------------- */

  const onImport = useCallback(
    async (file: File) => {
      try {
        const doc = parseWorkflow(await file.text());
        setNodes(doc.nodes);
        setEdges(doc.edges);
        setName(doc.name);
        setSummary({ kind: "idle" });
      } catch (error) {
        setSummary({ kind: "error", message: `Import failed: ${String(error)}` });
      }
    },
    [setNodes, setEdges],
  );

  const clearCanvas = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setSummary({ kind: "idle" });
  }, [setNodes, setEdges]);

  /* -- node types: memoized, or every render remounts the cards --------------- */
  const nodeTypes = useMemo(() => createNodeTypes(runSingleNode), [runSingleNode]);
  const edgeTypes = useMemo<EdgeTypes>(() => ({}), []);

  const descriptor = ENGINE_DESCRIPTORS.find((entry) => entry.provider === engine.provider)!;
  const EngineIcon = PROVIDER_ICONS[engine.provider];

  return (
    <div className="relative h-full w-full bg-obsidian-900">
      <EdgeGradient />

      {/* Ambient bloom so the obsidian ground isn't flat black. */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-40 -top-40 h-96 w-96 rounded-full bg-indigo-600/10 blur-3xl" />
        <div className="absolute -bottom-40 -right-32 h-96 w-96 rounded-full bg-violet-600/10 blur-3xl" />
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        minZoom={0.2}
        maxZoom={1.8}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ animated: true }}
        className="relative z-0"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={28}
          size={1}
          color="rgba(129,140,248,0.18)"
        />
        <Controls
          position="bottom-right"
          className="!overflow-hidden !rounded-xl !border !border-indigo-500/20 !shadow-glow"
        />
        <MiniMap
          position="top-right"
          pannable
          zoomable
          maskColor="rgba(7,10,17,0.7)"
          nodeColor={() => "#6366f1"}
          nodeStrokeColor="#818cf8"
        />
      </ReactFlow>

      {/* Title bar ---------------------------------------------------------- */}
      <div className="glass absolute left-4 top-4 z-10 flex items-center gap-3 rounded-xl px-4 py-2.5">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br
                         from-indigo-500 to-violet-500 text-xs font-bold text-white shadow-glow">
          CF
        </span>
        <div>
          <input
            className="w-52 bg-transparent text-sm font-semibold text-white outline-none
                       placeholder:text-slate-600"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Untitled workflow"
          />
          <p className="text-[10px] uppercase tracking-widest text-slate-500">
            {nodes.length} nodes · {edges.length} links
          </p>
        </div>
      </div>

      {/* Run summary -------------------------------------------------------- */}
      <div className="absolute left-4 top-24 z-10">
        <RunBanner summary={summary} />
      </div>

      {/* Floating control bar ------------------------------------------------ */}
      <div className="absolute bottom-6 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2
                      rounded-2xl border border-indigo-500/20 bg-obsidian-800/80 p-2
                      shadow-glow backdrop-blur-md">
        <AddNodeMenu onAdd={addNode} />

        <span className="h-6 w-px bg-white/10" />

        <button
          type="button"
          className="btn-ghost"
          onClick={() => setSettingsOpen(true)}
          title={`${descriptor.name} — ${descriptor.cost}`}
        >
          <EngineIcon size={14} />
          <span className="hidden sm:inline">{descriptor.name}</span>
          <Settings2 size={13} className="opacity-60" />
        </button>

        <span className="h-6 w-px bg-white/10" />

        <button
          type="button"
          className="btn-ghost"
          onClick={() => exportWorkflow(buildDocument(name, getNodes(), getEdges()))}
          title="Export workflow JSON"
        >
          <Download size={14} />
        </button>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => fileInput.current?.click()}
          title="Import workflow JSON"
        >
          <Upload size={14} />
        </button>
        <button
          type="button"
          className="btn-ghost hover:!border-rose-400/40 hover:!bg-rose-500/10 hover:!text-rose-200"
          onClick={clearCanvas}
          title="Clear canvas"
        >
          <Trash2 size={14} />
        </button>

        <span className="h-6 w-px bg-white/10" />

        <button
          type="button"
          className="btn-primary min-w-[9.5rem]"
          onClick={runWorkflow}
          disabled={running || nodes.length === 0}
        >
          {running ? (
            <>
              <Loader2 size={14} className="animate-spin" /> Running…
            </>
          ) : (
            <>
              <Play size={14} /> Run Workflow
            </>
          )}
        </button>
      </div>

      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void onImport(file);
          event.target.value = ""; // let the same file be re-imported
        }}
      />

      <SettingsModal
        open={settingsOpen}
        engine={engine}
        onChange={updateEngine}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}

/** ReactFlowProvider must wrap anything calling useReactFlow — including CanvasInner. */
export default function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
