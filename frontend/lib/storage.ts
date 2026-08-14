/** LocalStorage persistence + JSON workflow import/export. */

import {
  DEFAULT_ENGINE_CONFIG,
  STORAGE_KEYS,
  type CanvasEdge,
  type CanvasNode,
  type EngineConfig,
  type WorkflowDocument,
} from "@/types";

/** Reads are best-effort: a corrupt or absent key must never blank the canvas. */
function read<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota exceeded or private mode — the canvas still works in-memory */
  }
}

export function loadEngine(): EngineConfig {
  // Spread over the defaults so a config saved by an older build gains new fields.
  return { ...DEFAULT_ENGINE_CONFIG, ...(read<EngineConfig>(STORAGE_KEYS.engine) ?? {}) };
}

export function saveEngine(engine: EngineConfig): void {
  write(STORAGE_KEYS.engine, engine);
}

export function loadWorkflow(): WorkflowDocument | null {
  const doc = read<WorkflowDocument>(STORAGE_KEYS.workflow);
  return doc && Array.isArray(doc.nodes) && Array.isArray(doc.edges) ? doc : null;
}

export function saveWorkflow(
  name: string,
  nodes: CanvasNode[],
  edges: CanvasEdge[],
): void {
  write(STORAGE_KEYS.workflow, buildDocument(name, nodes, edges));
}

export function buildDocument(
  name: string,
  nodes: CanvasNode[],
  edges: CanvasEdge[],
): WorkflowDocument {
  return {
    version: 1,
    name,
    nodes,
    edges,
    updated_at: new Date().toISOString(),
  };
}

export function exportWorkflow(doc: WorkflowDocument): void {
  const blob = new Blob([JSON.stringify(doc, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${doc.name.replace(/[^\w-]+/g, "-").toLowerCase()}.canvasflow.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Parses an imported file, rejecting anything that isn't a workflow document. */
export function parseWorkflow(raw: string): WorkflowDocument {
  const parsed = JSON.parse(raw) as Partial<WorkflowDocument>;
  if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
    throw new Error("Not a CanvasFlow workflow: missing `nodes` or `edges` array.");
  }
  return {
    version: 1,
    name: parsed.name ?? "Imported workflow",
    nodes: parsed.nodes as CanvasNode[],
    edges: parsed.edges as CanvasEdge[],
    updated_at: parsed.updated_at ?? new Date().toISOString(),
  };
}
