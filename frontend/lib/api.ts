/** Thin client for the CanvasFlow execution backend. */

import type {
  CanvasEdge,
  CanvasNode,
  EngineConfig,
  EngineStatus,
  FlowEdgePayload,
  FlowNodePayload,
  IngestedSource,
  NodeResult,
  WorkflowResponse,
} from "@/types";

const API_BASE =
  process.env.NEXT_PUBLIC_CANVASFLOW_API ?? "http://localhost:8000";

/** Raised for anything that stops a request from returning a usable body. */
export class ApiError extends Error {}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(
      `Cannot reach the CanvasFlow backend at ${API_BASE}. Start it with ` +
        `\`uvicorn main:app --reload --port 8000\` from the backend/ directory.`,
    );
  }

  if (!response.ok) {
    // FastAPI errors arrive as {detail: ...}; fall back to raw text otherwise.
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (parsed.detail) detail = JSON.stringify(parsed.detail);
    } catch {
      /* not JSON — keep the raw body */
    }
    throw new ApiError(`Backend returned ${response.status}: ${detail.slice(0, 400)}`);
  }

  // DELETE answers 204 with no body, so an unconditional .json() would throw.
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return send<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Strip React Flow internals so the backend only sees what it needs. */
export function toNodePayload(node: CanvasNode): FlowNodePayload {
  return {
    id: node.id,
    type: node.type ?? "transformNode",
    data: {
      label: node.data.label,
      prompt: node.data.prompt,
      persona: node.data.persona,
      content: node.data.content,
      source_id: node.data.source?.source_id,
    },
    position: node.position,
  };
}

export function toEdgePayload(edge: CanvasEdge): FlowEdgePayload {
  return { id: edge.id, source: edge.source, target: edge.target };
}

export function executeWorkflow(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  engine: EngineConfig,
): Promise<WorkflowResponse> {
  return post<WorkflowResponse>("/api/execute/workflow", {
    nodes: nodes.map(toNodePayload),
    edges: edges.map(toEdgePayload),
    engine,
  });
}

export function executeNode(
  node: CanvasNode,
  upstreamOutputs: string[],
  engine: EngineConfig,
): Promise<NodeResult> {
  return post<NodeResult>("/api/execute/node", {
    node: toNodePayload(node),
    upstream_outputs: upstreamOutputs,
    engine,
  });
}

export function probeEngine(engine: EngineConfig): Promise<EngineStatus> {
  return post<EngineStatus>("/api/engine/status", engine);
}

/* ------------------------------------------------------------------- sources */

export function ingestUrl(url: string): Promise<IngestedSource> {
  return post<IngestedSource>("/api/sources/ingest", { url });
}

export function ingestFile(file: File): Promise<IngestedSource> {
  const form = new FormData();
  form.append("file", file);
  // No Content-Type header — only the browser can add the multipart boundary.
  return send<IngestedSource>("/api/sources/upload", { method: "POST", body: form });
}

export function deleteSource(sourceId: string): Promise<void> {
  return send<void>(`/api/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
}
