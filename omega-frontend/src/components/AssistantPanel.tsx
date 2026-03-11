"use client";

import { useState, useRef, useEffect } from "react";
import { Sparkles, Zap, ArrowUp, Loader2, Check, Copy, ClipboardCheck } from "lucide-react";
import { apiFetch } from "@/lib/api";

type StructuredSuggestion = {
  segment_id: number | string;
  suggested_text: string;
};

type AssistantMessage = {
  role: "user" | "assistant";
  content: string;
  suggestions?: StructuredSuggestion[];
};

interface AssistantPanelProps {
  jobId: string | null;
  onClose?: () => void;
  mode?: "modal" | "sidebar";
  onApplySuggestion?: (segmentId: number | string, newText: string) => void;
}

const QUICK_PROMPTS = [
  { label: "Tighten", prompt: "Tighten the wording, keep meaning, and keep line lengths readable." },
  { label: "Fix punctuation", prompt: "Fix punctuation and capitalization without changing meaning." },
  { label: "Broadcast tone", prompt: "Make the tone broadcast friendly and natural, keep it concise." },
];

/**
 * Try to extract structured suggestions from the assistant response.
 * Expects an array of {segment_id, suggested_text} objects in data.suggestions,
 * or falls back to parsing the response text for patterns like:
 *   Segment 12: "suggested text here"
 */
function parseSuggestions(data: Record<string, unknown>): StructuredSuggestion[] {
  // Prefer explicit structured field from the API
  if (Array.isArray(data.suggestions)) {
    return data.suggestions.filter(
      (s: unknown): s is StructuredSuggestion =>
        typeof s === "object" &&
        s !== null &&
        (typeof (s as StructuredSuggestion).segment_id === "number" || typeof (s as StructuredSuggestion).segment_id === "string") &&
        typeof (s as StructuredSuggestion).suggested_text === "string"
    );
  }

  // Fallback: try to parse from response text
  const text = String(data.response || "");
  const results: StructuredSuggestion[] = [];
  // Match patterns like: Segment 5: "some text here" or Segment #5: "text"
  const regex = /[Ss]egment\s*#?([\d_]+)\s*:\s*"([^"]+)"/g;
  let match;
  while ((match = regex.exec(text)) !== null) {
    results.push({
      segment_id: match[1],
      suggested_text: match[2],
    });
  }
  return results;
}

export function AssistantPanel({ jobId, onApplySuggestion }: AssistantPanelProps) {
  const [input, setInput] = useState("");
  // Start with an empty array so the Welcome state shows cleanly
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [appliedIds, setAppliedIds] = useState<Set<string>>(new Set());
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const handleApply = (suggestion: StructuredSuggestion) => {
    if (onApplySuggestion) {
      onApplySuggestion(suggestion.segment_id, suggestion.suggested_text);
      setAppliedIds((prev) => new Set(prev).add(`${suggestion.segment_id}-${suggestion.suggested_text}`));
    }
  };

  const handleCopy = async (text: string, msgIndex: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIdx(msgIndex);
      setTimeout(() => setCopiedIdx(null), 2000);
    } catch {
      // Clipboard API unavailable
    }
  };

  const handleSend = async () => {
    if (!input.trim() || !jobId) return;
    const newMsg: AssistantMessage = { role: "user", content: input };
    setMessages((p) => [...p, newMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await apiFetch("/api/assistant/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, message: newMsg.content, history: messages }),
      });
      const data = await res.json();
      const suggestions = parseSuggestions(data);
      setMessages((p) => [
        ...p,
        {
          role: "assistant",
          content: String(data.response || "Done."),
          suggestions: suggestions.length > 0 ? suggestions : undefined,
        },
      ]);
    } catch {
      setMessages((p) => [...p, { role: "assistant", content: "Error." }]);
    } finally {
      setLoading(false);
    }
  };

  if (!jobId) return null;

  return (
    <div className="flex flex-col h-full bg-[#0c1017] relative text-sm">

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto px-6 py-8 space-y-6 custom-scrollbar" ref={scrollRef}>

        {/* Welcome State - Only show when no messages yet */}
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-purple-600/20 to-indigo-600/20 flex items-center justify-center border border-purple-500/30 mb-5 shadow-lg shadow-purple-900/20">
              <Sparkles className="w-7 h-7 text-purple-400" />
            </div>
            <h3 className="text-base font-semibold text-gray-100 mb-2">Omega Copilot</h3>
            <p className="text-gray-500 max-w-[220px] leading-relaxed text-[13px]">
              Ready to help refine your subtitles. Select a segment or type a request below.
            </p>
          </div>
        )}

        {/* Message Bubbles */}
        {messages.map((m, i) => (
          <div key={i} className={`flex flex-col gap-2 ${m.role === "user" ? "items-end" : "items-start"}`}>

            {/* Role Label */}
            {m.role === "assistant" && (
              <div className="flex items-center gap-2 mb-1 opacity-70 pl-1">
                <Sparkles className="w-3 h-3 text-purple-400" />
                <span className="text-[10px] uppercase tracking-widest font-semibold text-gray-500">Copilot</span>
              </div>
            )}

            {/* Bubble */}
            <div
              className={`py-3.5 px-5 rounded-2xl max-w-[90%] text-[13px] leading-relaxed ${m.role === "assistant"
                ? "bg-[#161b22] border border-[#2d3748] text-gray-200 rounded-tl-md"
                : "bg-purple-600 text-white rounded-br-md"
                }`}
            >
              {m.content}

              {/* Structured Suggestions - Apply buttons */}
              {m.role === "assistant" && m.suggestions && m.suggestions.length > 0 && (
                <div className="mt-3 pt-3 border-t border-[#2d3748] space-y-2">
                  {m.suggestions.map((suggestion, si) => {
                    const key = `${suggestion.segment_id}-${suggestion.suggested_text}`;
                    const isApplied = appliedIds.has(key);
                    return (
                      <div
                        key={si}
                        className="flex items-start gap-2 bg-[#0d1117] rounded-lg p-3 border border-[#21262d]"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">
                            Segment {suggestion.segment_id}
                          </div>
                          <div className="text-[12px] text-gray-300 break-words">
                            {suggestion.suggested_text}
                          </div>
                        </div>
                        <button
                          onClick={() => handleApply(suggestion)}
                          disabled={isApplied}
                          className={`shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] font-medium transition-all ${
                            isApplied
                              ? "bg-green-500/10 text-green-400 border border-green-500/20 cursor-default"
                              : "bg-purple-600/20 text-purple-300 border border-purple-500/30 hover:bg-purple-600/30 hover:text-purple-200"
                          }`}
                        >
                          {isApplied ? (
                            <>
                              <Check className="w-3 h-3" />
                              Applied
                            </>
                          ) : (
                            <>
                              <Check className="w-3 h-3" />
                              Apply
                            </>
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Plain text assistant response - Copy button */}
              {m.role === "assistant" && (!m.suggestions || m.suggestions.length === 0) && (
                <div className="mt-2 pt-2 border-t border-[#2d3748] flex justify-end">
                  <button
                    onClick={() => handleCopy(m.content, i)}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-medium text-gray-500 hover:text-gray-300 hover:bg-[#21262d] transition-all"
                  >
                    {copiedIdx === i ? (
                      <>
                        <ClipboardCheck className="w-3 h-3 text-green-400" />
                        <span className="text-green-400">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3 h-3" />
                        Copy
                      </>
                    )}
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {/* Loading */}
        {loading && (
          <div className="flex items-center gap-3 text-gray-400 text-[12px] pl-1">
            <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
            <span>Thinking...</span>
          </div>
        )}
      </div>

      {/* Input Area */}
      <div className="p-5 pt-3 border-t border-[#1f2937] bg-[#0c1017]">

        {/* Quick Actions - spaced out properly */}
        <div className="flex gap-2.5 mb-4 overflow-x-auto pb-1 no-scrollbar">
          {QUICK_PROMPTS.map((item) => (
            <button
              key={item.label}
              onClick={() => setInput(item.prompt)}
              className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[#161b22] border border-[#2d3748] text-[12px] font-medium text-gray-300 hover:text-purple-300 hover:border-purple-500/40 hover:bg-purple-500/5 transition-all whitespace-nowrap"
            >
              <Zap className="w-3.5 h-3.5 text-purple-400/70" />
              {item.label}
            </button>
          ))}
        </div>

        {/* Input Field */}
        <form onSubmit={(e) => { e.preventDefault(); handleSend(); }} className="relative">
          <input
            className="w-full bg-[#161b22] border border-[#2d3748] text-gray-100 placeholder-gray-600 rounded-xl px-5 py-3.5 pr-12 text-[13px] focus:outline-none focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/20 transition-all"
            placeholder="Ask Copilot to edit..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 p-2 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
          >
            <ArrowUp className="w-4 h-4" />
          </button>
        </form>
      </div>
    </div>
  );
}
