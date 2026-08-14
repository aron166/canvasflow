"use client";

/**
 * Engine settings.
 *
 * Picks between the three execution backends and collects whatever credentials that
 * choice needs. Everything here lives in LocalStorage and travels with each execution
 * request — the backend stores none of it.
 */

import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Eye,
  EyeOff,
  HardDrive,
  KeyRound,
  Loader2,
  RadioTower,
  ShieldCheck,
  Terminal,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { probeEngine, ApiError } from "@/lib/api";
import {
  ENGINE_DESCRIPTORS,
  type EngineConfig,
  type EngineProvider,
  type EngineStatus,
} from "@/types";

const PROVIDER_ICONS: Record<EngineProvider, LucideIcon> = {
  anthropic_api: KeyRound,
  claude_cli: Terminal,
  ollama: HardDrive,
};

/* ------------------------------------------------------------------ primitives */

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-slate-400">
        {label}
      </span>
      {children}
      {hint ? <span className="mt-1.5 block text-xs text-slate-500">{hint}</span> : null}
    </label>
  );
}

function SecretInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <input
        type={visible ? "text" : "password"}
        className="field pr-10 font-mono text-xs"
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        onClick={() => setVisible((shown) => !shown)}
        aria-label={visible ? "Hide value" : "Show value"}
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-slate-500
                   transition hover:bg-white/5 hover:text-slate-300"
      >
        {visible ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

/* -------------------------------------------------------------- engine selector */

function EngineCard({
  provider,
  selected,
  onSelect,
}: {
  provider: EngineProvider;
  selected: boolean;
  onSelect: () => void;
}) {
  const descriptor = ENGINE_DESCRIPTORS.find((entry) => entry.provider === provider)!;
  const Icon = PROVIDER_ICONS[provider];

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`group relative overflow-hidden rounded-xl border p-4 text-left transition-all
                  ${
                    selected
                      ? "border-indigo-400/50 bg-indigo-500/10 shadow-glow"
                      : "border-indigo-500/15 bg-obsidian-950/40 hover:border-indigo-400/30 hover:bg-indigo-500/5"
                  }`}
    >
      {/* Selected cards get a soft top-edge bloom rather than a hard outline. */}
      {selected ? (
        <span className="pointer-events-none absolute -top-16 left-1/2 h-32 w-32 -translate-x-1/2
                         rounded-full bg-indigo-500/25 blur-2xl" />
      ) : null}

      <div className="relative flex items-start gap-3">
        <span
          className={`rounded-lg p-2 transition ${
            selected ? "bg-indigo-500/20 text-indigo-200" : "bg-white/5 text-slate-400"
          }`}
        >
          <Icon size={16} />
        </span>
        <div className="min-w-0">
          <p className={`text-sm font-semibold ${selected ? "text-white" : "text-slate-200"}`}>
            {descriptor.name}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-slate-400">{descriptor.tagline}</p>
          <span
            className={`mt-2 inline-block rounded-full px-2 py-0.5 text-[10px] font-medium ${
              selected
                ? "bg-indigo-400/20 text-indigo-100"
                : "bg-white/5 text-slate-400"
            }`}
          >
            {descriptor.cost}
          </span>
        </div>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ status line */

function StatusLine({
  status,
  checking,
  error,
}: {
  status: EngineStatus | null;
  checking: boolean;
  error: string | null;
}) {
  if (checking) {
    return (
      <p className="flex items-center gap-2 text-xs text-slate-400">
        <Loader2 size={13} className="animate-spin" /> Checking connection…
      </p>
    );
  }
  if (error) {
    return (
      <p className="flex items-start gap-2 text-xs text-amber-300">
        <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {error}
      </p>
    );
  }
  if (!status) {
    return (
      <p className="flex items-center gap-2 text-xs text-slate-500">
        <RadioTower size={13} /> Not checked yet.
      </p>
    );
  }
  return (
    <p
      className={`flex items-start gap-2 text-xs ${
        status.available ? "text-emerald-300" : "text-rose-300"
      }`}
    >
      {status.available ? (
        <CheckCircle2 size={13} className="mt-0.5 shrink-0" />
      ) : (
        <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      )}
      {status.detail}
    </p>
  );
}

/* ----------------------------------------------------------------------- modal */

export default function SettingsModal({
  open,
  engine,
  onChange,
  onClose,
}: {
  open: boolean;
  engine: EngineConfig;
  onChange: (engine: EngineConfig) => void;
  onClose: () => void;
}) {
  // Edits are staged locally and only committed on Save, so Cancel actually cancels.
  const [draft, setDraft] = useState<EngineConfig>(engine);
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setDraft(engine);
      setStatus(null);
      setProbeError(null);
    }
  }, [open, engine]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const patch = (fields: Partial<EngineConfig>) =>
    setDraft((current) => ({ ...current, ...fields }));

  const descriptor = ENGINE_DESCRIPTORS.find((entry) => entry.provider === draft.provider)!;

  async function testConnection() {
    setChecking(true);
    setProbeError(null);
    setStatus(null);
    try {
      setStatus(await probeEngine(draft));
    } catch (error) {
      setProbeError(
        error instanceof ApiError ? error.message : `Unexpected error: ${String(error)}`,
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Engine settings"
    >
      <div
        className="absolute inset-0 bg-obsidian-950/80 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="glass relative flex max-h-[88vh] w-full max-w-2xl animate-fade-up
                      flex-col overflow-hidden rounded-2xl shadow-glow">
        {/* Header ---------------------------------------------------------- */}
        <header className="flex items-center gap-3 border-b border-white/5 px-6 py-4">
          <span className="rounded-lg bg-indigo-500/15 p-2 text-indigo-300">
            <Cpu size={16} />
          </span>
          <div className="flex-1">
            <h2 className="text-sm font-semibold text-white">AI Engine</h2>
            <p className="text-xs text-slate-400">
              Choose how CanvasFlow executes every node in your workflow.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            className="rounded-lg p-2 text-slate-400 transition hover:bg-white/5 hover:text-white"
          >
            <X size={16} />
          </button>
        </header>

        {/* Body ------------------------------------------------------------ */}
        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          <div className="grid gap-3 sm:grid-cols-3">
            {ENGINE_DESCRIPTORS.map((entry) => (
              <EngineCard
                key={entry.provider}
                provider={entry.provider}
                selected={draft.provider === entry.provider}
                onSelect={() => {
                  patch({ provider: entry.provider });
                  setStatus(null);
                  setProbeError(null);
                }}
              />
            ))}
          </div>

          {/* Provider-specific credentials -------------------------------- */}
          <section className="space-y-4 rounded-xl border border-indigo-500/15 bg-obsidian-950/40 p-4">
            {draft.provider === "anthropic_api" ? (
              <>
                <Field
                  label="API key"
                  hint="Stored in this browser only. Leave blank to use ANTHROPIC_API_KEY from the backend's .env."
                >
                  <SecretInput
                    value={draft.anthropic_api_key ?? ""}
                    placeholder="sk-ant-…"
                    onChange={(anthropic_api_key) => patch({ anthropic_api_key })}
                  />
                </Field>
                <Field label="Model">
                  <input
                    className="field font-mono text-xs"
                    value={draft.anthropic_model}
                    spellCheck={false}
                    onChange={(event) => patch({ anthropic_model: event.target.value })}
                  />
                </Field>
              </>
            ) : null}

            {draft.provider === "claude_cli" ? (
              <>
                <Field
                  label="CLI binary"
                  hint="Absolute path if `claude` is not on the backend's PATH."
                >
                  <input
                    className="field font-mono text-xs"
                    value={draft.claude_cli_path}
                    spellCheck={false}
                    onChange={(event) => patch({ claude_cli_path: event.target.value })}
                  />
                </Field>
                <Field
                  label="Session token (optional)"
                  hint="Only needed if the backend host has no stored `claude` login. Otherwise leave blank."
                >
                  <SecretInput
                    value={draft.claude_session_token ?? ""}
                    placeholder="sk-ant-oat…"
                    onChange={(claude_session_token) => patch({ claude_session_token })}
                  />
                </Field>
              </>
            ) : null}

            {draft.provider === "ollama" ? (
              <>
                <Field label="Server URL">
                  <input
                    className="field font-mono text-xs"
                    value={draft.ollama_url}
                    spellCheck={false}
                    onChange={(event) => patch({ ollama_url: event.target.value })}
                  />
                </Field>
                <Field label="Model" hint="Must already be pulled: `ollama pull <model>`.">
                  <input
                    className="field font-mono text-xs"
                    value={draft.ollama_model}
                    spellCheck={false}
                    onChange={(event) => patch({ ollama_model: event.target.value })}
                  />
                </Field>
              </>
            ) : null}

            <Field label="Timeout (seconds)" hint="Per node. Raise it for long generations on slow local models.">
              <input
                type="number"
                min={10}
                max={3600}
                className="field w-32 font-mono text-xs"
                value={draft.timeout_seconds}
                onChange={(event) =>
                  patch({ timeout_seconds: Number(event.target.value) || 300 })
                }
              />
            </Field>

            <div className="flex flex-wrap items-center gap-3 border-t border-white/5 pt-4">
              <button type="button" className="btn-ghost" onClick={testConnection} disabled={checking}>
                <RadioTower size={14} /> Test connection
              </button>
              <div className="min-w-0 flex-1">
                <StatusLine status={status} checking={checking} error={probeError} />
              </div>
            </div>
          </section>

          {/* Setup checklist ----------------------------------------------- */}
          <section className="rounded-xl border border-white/5 bg-obsidian-950/30 p-4">
            <p className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase
                          tracking-widest text-slate-400">
              <ShieldCheck size={13} /> {descriptor.name} requires
            </p>
            <ul className="space-y-1.5">
              {descriptor.requirements.map((requirement) => (
                <li key={requirement} className="flex gap-2 text-xs text-slate-400">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-indigo-400/70" />
                  <code className="font-mono text-[11px] text-slate-300">{requirement}</code>
                </li>
              ))}
            </ul>
          </section>
        </div>

        {/* Footer ---------------------------------------------------------- */}
        <footer className="flex items-center justify-between gap-3 border-t border-white/5
                           bg-obsidian-950/40 px-6 py-4">
          <p className="text-[11px] text-slate-500">
            Credentials never leave your browser except to your own backend.
          </p>
          <div className="flex gap-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => {
                onChange(draft);
                onClose();
              }}
            >
              Save engine
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
