/**
 * CanvasFlow — shared type definitions.
 *
 * This file is the contract with `backend/main.py`. Field names are snake_case
 * wherever they cross the wire, matching the Pydantic models exactly; camelCase
 * appears only on types that never leave the browser.
 */

import type { Edge, Node } from "@xyflow/react";

/* --------------------------------------------------------------------- Sources */

/** What a source node ingested. Mirrors `SourceKind` in backend/ingest.py. */
export type SourceKind = "youtube" | "webpage" | "document" | "media" | "text";

/** How a source's text reached the model on the last run. */
export type RetrievalStrategy = "whole" | "embeddings" | "keyword" | "full";

/**
 * How much of an oversized source to put in front of the model.
 *
 * - `auto`     — whole source when it fits, relevant passages when it doesn't
 * - `retrieve` — always passages; fastest, one model call
 * - `full`     — read every part in sequence, then synthesise; N+1 calls, nothing skipped
 */
export type CoverageMode = "auto" | "retrieve" | "full";

/** A resolved source, returned by `/api/sources/ingest`. */
export interface IngestedSource {
  source_id: string;
  kind: SourceKind;
  title: string;
  origin: string;
  char_count: number;
  /** First ~1200 chars, for the node card preview. */
  preview: string;
  meta: {
    duration_label?: string;
    pages?: number;
    site?: string;
    format?: string;
    whisper_model?: string;
    video_id?: string;
  };
}

/** Per-source report of how context was assembled, surfaced on the node card. */
export interface RetrievalReport {
  source_id: string;
  strategy: RetrievalStrategy;
  used_chars: number;
  total_chars: number;
  chunks_used: number;
  chunks_total: number;
  note: string;
}

/** Human-readable label per source kind, for node headers and the add menu. */
export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  youtube: "YouTube",
  webpage: "Web page",
  document: "Document",
  media: "Audio / video",
  text: "Pasted text",
};

/* ------------------------------------------------------------------ AI engines */

/** The three interchangeable execution backends. */
export type EngineProvider = "anthropic_api" | "claude_cli" | "ollama";

/** Credentials and knobs for the active engine. Sent with every execution request
 *  and persisted to LocalStorage only — never stored on the server. */
export interface EngineConfig {
  provider: EngineProvider;

  /** Option A — paid developer API key. */
  anthropic_api_key?: string;
  anthropic_model: string;

  /** Option B — flat-rate subscription via an authenticated Claude Code CLI session. */
  claude_cli_path: string;
  /** Optional OAuth/session token; omit to use the CLI's own stored login. */
  claude_session_token?: string;

  /** Option C — free local inference. */
  ollama_url: string;
  ollama_model: string;

  temperature?: number;
  timeout_seconds: number;
}

/** Static metadata backing the engine picker in Settings. */
export interface EngineDescriptor {
  provider: EngineProvider;
  name: string;
  tagline: string;
  cost: string;
  /** Rendered as a short setup checklist under the selected engine. */
  requirements: string[];
}

/** Live reachability result from `POST /api/engine/status`. */
export interface EngineStatus {
  provider: EngineProvider;
  available: boolean;
  detail: string;
}

export const DEFAULT_ENGINE_CONFIG: EngineConfig = {
  provider: "ollama",
  anthropic_model: "claude-opus-5",
  claude_cli_path: "claude",
  ollama_url: "http://localhost:11434",
  ollama_model: "llama3",
  timeout_seconds: 300,
};

export const ENGINE_DESCRIPTORS: readonly EngineDescriptor[] = [
  {
    provider: "anthropic_api",
    name: "Anthropic API",
    tagline: "Official SDK. Highest quality, fully hosted.",
    cost: "Pay per token",
    requirements: [
      "An API key from console.anthropic.com",
      "Outbound network access from the backend host",
    ],
  },
  {
    provider: "claude_cli",
    name: "Claude Subscription",
    tagline: "Bridges your flat-rate plan through a local Claude Code session.",
    cost: "Included in your plan",
    requirements: [
      "npm install -g @anthropic-ai/claude-code",
      "Run `claude` once on the backend host and log in",
      "Backend must run on the same machine as that session",
    ],
  },
  {
    provider: "ollama",
    name: "Local Ollama",
    tagline: "Runs entirely on your machine. Nothing leaves the device.",
    cost: "Free",
    requirements: [
      "Ollama installed and `ollama serve` running",
      "A pulled model, e.g. `ollama pull llama3`",
    ],
  },
] as const;

/* -------------------------------------------------------------------- Node model */

/** Node kinds the canvas can render. Each maps to a system prompt on the backend. */
export type CanvasNodeType =
  | "sourceNode"
  | "campaignInput"
  | "copywriting"
  | "videoScript"
  | "researchNode"
  | "transformNode"
  | "output";

/** Per-node execution lifecycle, driving the card's border and status pill. */
export type NodeRunStatus = "idle" | "running" | "ok" | "error" | "skipped";

/**
 * Data carried by every node.
 *
 * `content` is static text the user typed in; `prompt` is the instruction applied
 * to that content plus anything arriving from upstream. Keeping them separate is
 * what lets one node be reused with different instructions.
 */
export interface CanvasNodeData extends Record<string, unknown> {
  label: string;
  /** The instruction for this node. */
  prompt: string;
  /** Optional voice/persona layered on top of the node type's system prompt. */
  persona?: string;
  /** Static source material pasted into the node. */
  content?: string;
  /** Result of the last run. */
  output?: string;
  status?: NodeRunStatus;
  error?: string;
  durationMs?: number;

  /* -- source nodes only -------------------------------------------------- */
  /** The URL the user pasted, before ingestion. */
  url?: string;
  /** Set once ingestion succeeds; its `source_id` is what execution references. */
  source?: IngestedSource;
  /** True while ingestion is in flight. */
  ingesting?: boolean;
  /** How much of an oversized source to send. Defaults to "auto" when unset. */
  coverage?: CoverageMode;
  /** How this source's text reached the model on the last run. */
  retrieval?: RetrievalReport;
}

/** A React Flow node narrowed to CanvasFlow's data shape. */
export type CanvasNode = Node<CanvasNodeData, CanvasNodeType>;
export type CanvasEdge = Edge;

/** Persona options offered per node type, surfaced as the node's dropdown. */
export const NODE_PERSONAS: Record<CanvasNodeType, readonly string[]> = {
  // A source node ingests rather than generates, so it has no persona.
  sourceNode: [],
  campaignInput: [
    "Growth strategist",
    "Brand planner",
    "Performance marketer",
    "Founder voice",
  ],
  copywriting: [
    "Direct response",
    "Luxury brand",
    "Dry and deadpan",
    "Technical B2B",
    "Playful DTC",
  ],
  videoScript: [
    "TikTok hook-first",
    "YouTube long-form",
    "Explainer / educational",
    "Cinematic brand film",
  ],
  researchNode: [
    "Competitive analyst",
    "Audience researcher",
    "Skeptical fact-checker",
  ],
  transformNode: ["Neutral rewriter", "Summarizer", "Translator", "Formatter"],
  output: ["Editor", "Proofreader", "Publisher"],
};

/* --------------------------------------------------------------- Canvas + storage */

/** The exportable workflow document — the JSON that `Export` writes and `Import` reads. */
export interface WorkflowDocument {
  version: 1;
  name: string;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  updated_at: string;
}

/** Everything persisted to LocalStorage. */
export interface PersistedState {
  workflow: WorkflowDocument;
  engine: EngineConfig;
}

export const STORAGE_KEYS = {
  workflow: "canvasflow.workflow.v1",
  engine: "canvasflow.engine.v1",
} as const;

/* ------------------------------------------------------------- Execution wire types */

/** Node as the backend sees it — position and React Flow internals are dropped. */
export interface FlowNodePayload {
  id: string;
  type: string;
  data: {
    label: string;
    prompt: string;
    persona?: string;
    content?: string;
    /** Set on source nodes: the backend loads this source's text at run time. */
    source_id?: string;
    coverage?: CoverageMode;
  };
  position?: { x: number; y: number };
}

export interface FlowEdgePayload {
  id?: string;
  source: string;
  target: string;
}

export interface ExecuteWorkflowRequest {
  nodes: FlowNodePayload[];
  edges: FlowEdgePayload[];
  engine: EngineConfig;
}

export interface ExecuteNodeRequest {
  node: FlowNodePayload;
  upstream_outputs: string[];
  engine: EngineConfig;
}

export interface NodeResult {
  node_id: string;
  status: "ok" | "error" | "skipped";
  output: string;
  error?: string | null;
  provider?: EngineProvider | null;
  model?: string | null;
  duration_ms: number;
  /** Present when this node consumed sources; one entry per source used. */
  retrieval?: RetrievalReport[] | null;
}

export interface WorkflowResponse {
  status: "ok" | "partial" | "error";
  results: NodeResult[];
  order: string[];
  duration_ms: number;
}
