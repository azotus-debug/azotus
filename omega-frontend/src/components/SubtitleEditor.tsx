"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import {
  Save,
  Loader2,
  Clock,
  RotateCcw,
  Merge,
  Split,
  Trash2,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Maximize2,
  Minimize2
} from "lucide-react";
import Link from "next/link";
import { AssistantPanel } from "@/components/AssistantPanel";
import { AIQualityPanel } from "@/components/AIQualityPanel";
import { VersionHistoryPanel } from "@/components/VersionHistoryPanel";
import { OnScreenTextCapture } from "@/components/OnScreenTextCapture";
import { GraphicZonesPanel, GraphicZone } from "@/components/GraphicZonesPanel";

interface Segment {
  id?: number;
  start: number;
  end: number;
  text: string;
  source_text?: string;
}

interface SubtitleEditorProps {
  jobId: string;
  initialSegments: Segment[];
  initialGraphicZones?: GraphicZone[];
  track?: TrackInfo | null;
}

interface TrackInfo {
  language_code?: string;
  meta?: {
    editor_report?: unknown;
    target_language?: string;
    [key: string]: unknown;
  };
}


const formatTimecode = (value: number) => {
  if (!Number.isFinite(value)) return "--:--:--:--";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = Math.floor(value % 60);
  const frames = Math.floor((value % 1) * 24);
  return [hours, minutes, seconds].map((v) => v.toString().padStart(2, "0")).join(":") + ":" + frames.toString().padStart(2, "0");
};

// Mirrors subtitle_standards.py to keep UI warnings aligned with finalizer rules.
const MAX_CHARS_PER_LINE = 42;
const MAX_LINES = 2;
const MAX_CHARS_TOTAL = MAX_CHARS_PER_LINE * MAX_LINES;
const MIN_DURATION = 1.0;
const LANGUAGE_CPS: Record<string, { ideal: number; tight: number }> = {
  is: { ideal: 14.0, tight: 17.0 },
  de: { ideal: 15.0, tight: 18.0 },
  en: { ideal: 17.0, tight: 20.0 },
  es: { ideal: 18.0, tight: 21.0 },
  pt: { ideal: 17.0, tight: 20.0 },
  fr: { ideal: 16.0, tight: 19.0 },
  it: { ideal: 17.0, tight: 20.0 },
};

const getCpsTargets = (lang?: string) => {
  const key = (lang || "en").toLowerCase();
  return LANGUAGE_CPS[key] || { ideal: 17.0, tight: 20.0 };
};

type QCWarning = { type: "cps" | "duration" | "length" | "line"; level: "warn" | "error"; msg: string };

const getWarnings = (segment: Segment, lang?: string): QCWarning[] => {
  const warnings: QCWarning[] = [];
  const rawText = segment.text || "";
  const normalizedText = rawText.replace(/\s+/g, " ").trim();
  if (!normalizedText) return warnings;

  const duration = Number.isFinite(segment.end - segment.start) ? segment.end - segment.start : 0;
  const { ideal, tight } = getCpsTargets(lang);
  const cps = duration > 0 ? normalizedText.length / duration : 0;

  if (cps > tight) {
    warnings.push({ type: "cps", level: "error", msg: `CPS ${cps.toFixed(1)} > ${tight}` });
  } else if (cps > ideal) {
    warnings.push({ type: "cps", level: "warn", msg: `CPS ${cps.toFixed(1)} > ${ideal}` });
  }

  if (duration < MIN_DURATION) {
    warnings.push({ type: "duration", level: "warn", msg: `Duration ${duration.toFixed(2)}s < ${MIN_DURATION}` });
  }

  const lines = rawText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const longestLine = lines.length ? Math.max(...lines.map((line) => line.length)) : normalizedText.length;
  if (longestLine > MAX_CHARS_PER_LINE) {
    warnings.push({ type: "line", level: "warn", msg: `Line ${longestLine} > ${MAX_CHARS_PER_LINE}` });
  }

  if (normalizedText.length > MAX_CHARS_TOTAL) {
    warnings.push({ type: "length", level: "warn", msg: `Total ${normalizedText.length} > ${MAX_CHARS_TOTAL}` });
  }

  return warnings;
};

// Simple Accordion Components
const AccordionItem = ({
  title,
  isOpen,
  onToggle,
  children,
  icon: Icon
}: {
  title: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  icon?: React.ElementType;
}) => (
  <div className="border-b border-subtle last:border-b-0 flex flex-col min-h-0 shrink-0">
    <button
      onClick={onToggle}
      className="flex items-center justify-between px-4 py-3 hover:bg-white/[0.02] transition-colors text-sm font-medium text-gray-300 select-none shrink-0"
    >
      <div className="flex items-center gap-2">
        {Icon && <Icon className="w-4 h-4 text-muted" />}
        <span>{title}</span>
      </div>
      {isOpen ? <ChevronDown className="w-4 h-4 text-muted" /> : <ChevronRight className="w-4 h-4 text-muted" />}
    </button>
    {isOpen && (
      <div className="flex-1 min-h-0 overflow-hidden flex flex-col animate-in slide-in-from-top-1 duration-200">
        <div className="p-3 pt-0 flex-1 overflow-y-auto min-h-0 custom-scrollbar">
          {children}
        </div>
      </div>
    )}
  </div>
);

export function SubtitleEditor({ jobId, initialSegments, initialGraphicZones = [], track }: SubtitleEditorProps) {
  const [segments, setSegments] = useState<Segment[]>(initialSegments);
  const trackInfo = track ?? null;
  const trackLanguage = (trackInfo?.language_code || trackInfo?.meta?.target_language || 'is').toLowerCase();
  const editorReportRaw = trackInfo?.meta?.editor_report;
  const [saving, setSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const activeSegmentRef = useRef<HTMLDivElement>(null);

  const [history, setHistory] = useState<Segment[][]>([]);
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());
  const [flaggedSegmentIds, setFlaggedSegmentIds] = useState<Set<number>>(new Set());
  const [graphicZones, setGraphicZones] = useState<GraphicZone[]>(initialGraphicZones);
  const [isMarkingZone, setIsMarkingZone] = useState(false);
  const [pendingZoneStart, setPendingZoneStart] = useState<number | null>(null);
  const [showOnlyIssues, setShowOnlyIssues] = useState(false);

  // Sidebar State
  const [activeAccordion, setActiveAccordion] = useState<string>("quality");
  const [copilotExpanded, setCopilotExpanded] = useState(true);

  // React to flagged segments from track metadata
  useEffect(() => {
    if (!editorReportRaw) return;
    try {
      const report = typeof editorReportRaw === "string"
        ? JSON.parse(editorReportRaw)
        : editorReportRaw;
      const flagged = report.flagged_segments || [];
      setFlaggedSegmentIds(new Set(flagged.map((f: { id: number }) => f.id)));
    } catch (err) {
      console.error("Failed to parse editor report", err);
    }
  }, [editorReportRaw]);

  useEffect(() => {
    if (history.length === 0 && initialSegments.length > 0) setSegments(initialSegments);
  }, [initialSegments]);

  const pushHistory = () => setHistory((prev) => [...prev.slice(-49), segments]);

  const handleUndo = () => {
    if (history.length === 0) return;
    const previous = history[history.length - 1];
    setSegments(previous);
    setHistory((prev) => prev.slice(0, -1));
    setSelectedIndices(new Set());
  };

  const warningsByIndex = useMemo(
    () => segments.map((segment) => getWarnings(segment, trackLanguage)),
    [segments, trackLanguage]
  );

  const warningSummary = useMemo(() => {
    let segmentsWithIssues = 0;
    let errorSegments = 0;
    let warnSegments = 0;
    warningsByIndex.forEach((warnings) => {
      if (warnings.length === 0) return;
      segmentsWithIssues += 1;
      if (warnings.some((warning) => warning.level === "error")) {
        errorSegments += 1;
      } else {
        warnSegments += 1;
      }
    });
    return {
      totalSegments: segments.length,
      segmentsWithIssues,
      errorSegments,
      warnSegments,
    };
  }, [warningsByIndex, segments.length]);

  const visibleSegments = useMemo(() => {
    const items = segments.map((segment, index) => ({
      segment,
      index,
      warnings: warningsByIndex[index] || [],
    }));
    if (!showOnlyIssues) return items;
    return items.filter((item) => item.warnings.length > 0);
  }, [segments, warningsByIndex, showOnlyIssues]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const update = () => setCurrentTime(video.currentTime);
    const play = () => setIsPlaying(true);
    const pause = () => setIsPlaying(false);
    const loadMeta = () => setDuration(video.duration || 0);

    video.addEventListener("timeupdate", update);
    video.addEventListener("play", play);
    video.addEventListener("pause", pause);
    video.addEventListener("loadedmetadata", loadMeta);

    return () => {
      video.removeEventListener("timeupdate", update);
      video.removeEventListener("play", play);
      video.removeEventListener("pause", pause);
      video.removeEventListener("loadedmetadata", loadMeta);
    };
  }, []);

  // Keyboard shortcuts: J (prev), K (play/pause), L (next), Space (play/pause)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger shortcuts when typing in input fields
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) {
        return;
      }

      switch (e.key.toLowerCase()) {
        case "j":
          e.preventDefault();
          seekPrev();
          break;
        case "k":
        case " ": // Space bar
          e.preventDefault();
          togglePlay();
          break;
        case "l":
          e.preventDefault();
          seekNext();
          break;
        case "s":
          if (e.metaKey || e.ctrlKey) {
            e.preventDefault();
            handleSave();
          }
          break;
        case "z":
          if (e.metaKey || e.ctrlKey) {
            e.preventDefault();
            handleUndo();
          }
          break;
        case "g":
          e.preventDefault();
          // Toggle zone marking
          if (!isMarkingZone) {
            setPendingZoneStart(currentTime);
            setIsMarkingZone(true);
          } else {
            // End zone
            if (pendingZoneStart !== null) {
              const newZone: GraphicZone = {
                id: `zone-${Date.now()}`,
                startTime: Math.min(pendingZoneStart, currentTime),
                endTime: Math.max(pendingZoneStart, currentTime),
                label: `Graphic ${graphicZones.length + 1}`,
                position: "top",
              };
              setGraphicZones((prev) => [...prev, newZone].sort((a, b) => a.startTime - b.startTime));
            }
            setPendingZoneStart(null);
            setIsMarkingZone(false);
          }
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [segments, selectedIndices]); // Re-bind when segments change

  const togglePlay = () => {
    if (videoRef.current) {
      if (videoRef.current.paused) videoRef.current.play();
      else videoRef.current.pause();
    }
  };

  const handleSeek = (value: number) => {
    if (!videoRef.current) return;
    videoRef.current.currentTime = value;
    setCurrentTime(value);
  };

  const handleSegmentChange = (index: number, field: keyof Segment, value: string | number) => {
    const newSegments = [...segments];
    newSegments[index] = { ...newSegments[index], [field]: value };
    setSegments(newSegments);
  };

  const handleSelect = (index: number, multi: boolean) => {
    if (multi) {
      const newSet = new Set(selectedIndices);
      if (newSet.has(index)) newSet.delete(index);
      else newSet.add(index);
      setSelectedIndices(newSet);
    } else {
      setSelectedIndices(new Set([index]));
    }
  };

  const handleMerge = () => {
    const sorted = Array.from(selectedIndices).sort((a, b) => a - b);
    if (sorted.length < 2) return;
    pushHistory();
    const firstIdx = sorted[0];
    const newSeg = { ...segments[firstIdx] };
    for (let i = 1; i < sorted.length; i++) {
      const nextSeg = segments[sorted[i]];
      newSeg.end = Math.max(newSeg.end, nextSeg.end);
      newSeg.text = (newSeg.text.trim() + " " + nextSeg.text.trim()).trim();
    }
    const toRemove = new Set(sorted.slice(1));
    setSegments(segments.filter((_, i) => !toRemove.has(i)));
    setSelectedIndices(new Set([firstIdx]));
  };

  const handleSplit = () => {
    if (selectedIndices.size !== 1) return;
    const idx = Array.from(selectedIndices)[0];
    const seg = segments[idx];
    pushHistory();

    let splitTime = currentTime;
    if (splitTime <= seg.start || splitTime >= seg.end) splitTime = (seg.start + seg.end) / 2;

    const words = seg.text.split(" ");
    const midWord = Math.floor(words.length / 2);

    const newSeg1 = { ...seg, end: splitTime, text: words.slice(0, midWord).join(" ") };
    const newSeg2 = { ...seg, start: splitTime, text: words.slice(midWord).join(" ") };

    const newSegments = [...segments];
    newSegments.splice(idx, 1, newSeg1, newSeg2);
    setSegments(newSegments);
    setSelectedIndices(new Set([idx, idx + 1]));
  };

  const handleDelete = () => {
    if (selectedIndices.size === 0) return;
    pushHistory();
    setSegments(segments.filter((_, i) => !selectedIndices.has(i)));
    setSelectedIndices(new Set());
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`/api/editor/${jobId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          segments,
          graphic_zones: graphicZones,
          history: history
        }),
      });
      setLastSaved(new Date());
    } catch {
      alert("Save failed");
    } finally {
      setSaving(false);
    }
  };

  const activeIndex = segments.findIndex((s) => currentTime >= s.start && currentTime < s.end);
  const activeSegment = activeIndex >= 0 ? segments[activeIndex] : null;

  useEffect(() => {
    if (activeIndex !== -1 && activeSegmentRef.current && !selectedIndices.has(activeIndex)) {
      activeSegmentRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeIndex, selectedIndices]);

  const seekPrev = () => {
    if (!segments.length) return;
    const targetIndex = Math.max(0, activeIndex > 0 ? activeIndex - 1 : 0);
    const target = segments[targetIndex];
    handleSeek(target.start);
    setSelectedIndices(new Set([targetIndex]));
  };

  const seekNext = () => {
    if (!segments.length) return;
    const targetIndex = Math.min(segments.length - 1, activeIndex >= 0 ? activeIndex + 1 : 0);
    const target = segments[targetIndex];
    handleSeek(target.start);
    setSelectedIndices(new Set([targetIndex]));
  };

  return (
    <div className="flex h-screen w-screen flex-col bg-[rgb(10,10,12)] overflow-hidden">
      {/* Header */}
      <header className="h-12 flex items-center justify-between px-4 border-b border-subtle surface-1 shrink-0">
        <div className="flex items-center gap-4">
          <Link href="/" className="btn btn-ghost p-2">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <div className="label">Workstation</div>
            <div className="text-sm font-medium text-primary truncate max-w-[200px]">{jobId}</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {lastSaved && (
            <span className="pill text-[10px]">
              <Clock className="w-3 h-3" />
              Saved {lastSaved.toLocaleTimeString()}
            </span>
          )}
          <button onClick={handleSave} disabled={saving} className="btn btn-primary">
            {saving ? <Loader2 className="w-4 h-4 spin" /> : <Save className="w-4 h-4" />}
            Save
          </button>
        </div>
      </header>

      {/* Main Layout: Video + Segments + Right Sidebar */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Video + Transport + Segment List - with left padding for edge spacing */}
        <div className="flex-1 flex flex-col min-w-0 pl-6 overflow-hidden">
          {/* Video Container - Fixed height, never scrolls away */}
          <div className="h-[50%] min-h-[300px] flex flex-col bg-black border-b border-subtle shrink-0">
            <div className="flex-1 relative flex items-center justify-center min-h-0 overflow-hidden">
              <video
                ref={videoRef}
                src={`/api/stream/${jobId}`}
                className="max-w-full max-h-full object-contain rounded-sm"
                onClick={togglePlay}
              />
              {/* Subtitle Overlay - Lower third with inline styles to ensure visibility */}
              {activeSegment && (
                <div style={{ position: 'absolute', bottom: '16px', left: 0, right: 0, display: 'flex', justifyContent: 'center', pointerEvents: 'none' }}>
                  <div style={{ backgroundColor: '#000000', padding: '8px 16px' }}>
                    <p style={{ color: '#ffffff', fontSize: '18px', textAlign: 'center', margin: 0, lineHeight: 1.4, whiteSpace: 'pre-wrap' }}>
                      {activeSegment.text}
                    </p>
                  </div>
                </div>
              )}
            </div>

            {/* Transport Controls */}
            <div className="px-4 py-3 border-t border-subtle surface-1 shrink-0">
              <div className="flex items-center justify-between gap-4 mb-2">
                <span className="font-mono text-cyan text-sm tracking-wide w-28">{formatTimecode(currentTime)}</span>
                <div className="flex items-center gap-1">
                  <button onClick={seekPrev} className="btn btn-ghost p-2">
                    <SkipBack className="w-4 h-4" />
                  </button>
                  <button onClick={togglePlay} className="btn btn-primary p-3 rounded-full">
                    {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                  </button>
                  <button onClick={seekNext} className="btn btn-ghost p-2">
                    <SkipForward className="w-4 h-4" />
                  </button>
                </div>
                <span className="font-mono text-muted text-xs w-28 text-right">{formatTimecode(duration)}</span>
              </div>
              <input
                type="range"
                min={0}
                max={duration || 0}
                step="0.01"
                value={currentTime}
                onChange={(e) => handleSeek(parseFloat(e.target.value))}
                className="w-full accent-cyan-500"
              />
            </div>
          </div>

          {/* Segment Editor - This section scrolls independently */}
          <div className="flex-1 flex flex-col surface-1 min-h-0 overflow-hidden">
            {/* Toolbar */}
            <div className="h-10 flex items-center justify-between px-5 border-b border-subtle shrink-0">
              <div className="flex items-center gap-1">
                <button onClick={handleUndo} disabled={history.length === 0} className="btn btn-ghost p-2 disabled:opacity-40">
                  <RotateCcw className="w-4 h-4" />
                </button>
                <div className="w-px h-4 bg-[rgba(255,255,255,0.1)] mx-1"></div>
                <button onClick={handleMerge} disabled={selectedIndices.size < 2} className="btn btn-ghost text-xs disabled:opacity-40">
                  <Merge className="w-3.5 h-3.5" /> Merge
                </button>
                <button onClick={handleSplit} disabled={selectedIndices.size !== 1} className="btn btn-ghost text-xs disabled:opacity-40">
                  <Split className="w-3.5 h-3.5" /> Split
                </button>
                <button onClick={handleDelete} disabled={selectedIndices.size === 0} className="btn btn-ghost text-xs text-rose disabled:opacity-40">
                  <Trash2 className="w-3.5 h-3.5" /> Delete
                </button>
              </div>
              <span className="text-xs text-muted">{segments.length} segments</span>
            </div>

            <div className="qc-summary">
              <div className="qc-summary-left">
                <span className="qc-summary-title">QC</span>
                {warningSummary.segmentsWithIssues > 0 ? (
                  <span className="qc-summary-counts">
                    {warningSummary.errorSegments} errors · {warningSummary.warnSegments} warnings ·{" "}
                    {warningSummary.segmentsWithIssues} segments
                  </span>
                ) : (
                  <span className="qc-summary-clean">All clear</span>
                )}
              </div>
              <button
                type="button"
                className={`qc-toggle${showOnlyIssues ? " active" : ""}`}
                onClick={() => setShowOnlyIssues((prev) => !prev)}
                disabled={warningSummary.segmentsWithIssues === 0}
              >
                Show only issues
              </button>
            </div>

            {/* Segment List - Scrollable */}
            <div className="flex-1 overflow-y-auto min-h-0">
              {/* Header Row - Professional spacing */}
              <div className="grid grid-cols-[120px_120px_1fr_1fr] border-b border-subtle sticky top-0 z-10 surface-2 text-[11px] text-muted font-medium uppercase tracking-wider shadow-sm">
                <div className="px-5 py-4 border-r border-subtle">Start Time</div>
                <div className="px-5 py-4 border-r border-subtle">End Time</div>
                <div className="px-5 py-4 border-r border-subtle">Original {trackLanguage === 'is' ? '(English)' : 'Text'}</div>
                <div className="px-5 py-4">Translation ({trackLanguage.toUpperCase() || 'IS'})</div>
              </div>

              {/* Segment Rows */}
              {showOnlyIssues && warningSummary.segmentsWithIssues === 0 && (
                <div className="qc-empty">No QC issues detected.</div>
              )}
              {visibleSegments.map(({ segment: seg, index, warnings }) => {
                const isActive = index === activeIndex;
                const isSelected = selectedIndices.has(index);
                const isFlagged = seg.id !== undefined && flaggedSegmentIds.has(seg.id);
                const warningLevel = warnings.some((w) => w.level === "error") ? "error" : "warn";
                return (
                  <div
                    key={index}
                    ref={isActive ? activeSegmentRef : null}
                    onClick={(e) => {
                      handleSelect(index, e.metaKey || e.ctrlKey || e.shiftKey);
                      if (!isSelected && videoRef.current) videoRef.current.currentTime = seg.start;
                    }}
                    className={`grid grid-cols-[120px_120px_1fr_1fr] border-b text-sm cursor-pointer transition-all duration-75 ease-out ${warnings.length > 0 ? (warningLevel === "error" ? "segment-row--error" : "segment-row--warn") : ""} ${isFlagged ? "border-l-2 border-l-amber-500 bg-amber-500/5" : ""
                      } ${isSelected
                        ? "bg-cyan-500/10 border-l-2 border-l-cyan-500"
                        : isActive
                          ? "bg-white/5"
                          : "hover:bg-white/[0.02]"
                      } border-subtle`}
                    title={isFlagged ? "⚠️ Flagged for review" : undefined}
                  >
                    {/* IN Timecode */}
                    <div
                      className="px-5 py-4 border-r border-subtle font-mono text-emerald-400 cursor-text flex items-center gap-2 tracking-wider text-[13px]"
                      onClick={(e) => {
                        e.stopPropagation();
                        // Seek video when clicking IN timecode
                        if (videoRef.current) videoRef.current.currentTime = seg.start;
                        setSelectedIndices(new Set([index]));
                      }}
                    >
                      {formatTimecode(seg.start)}
                      {warnings.length > 0 && (
                        <span
                          className={`qc-warning qc-warning--${warningLevel}`}
                          title={warnings.map((w) => w.msg).join(" • ")}
                        >
                          ⚠️
                        </span>
                      )}
                    </div>
                    {/* OUT Timecode */}
                    <div
                      className="px-5 py-4 border-r border-subtle font-mono text-rose-400 cursor-text flex items-center tracking-wider text-[13px]"
                      onClick={(e) => {
                        e.stopPropagation();
                        // Seek video when clicking OUT timecode (seek to start of segment)
                        if (videoRef.current) videoRef.current.currentTime = seg.start;
                        setSelectedIndices(new Set([index]));
                      }}
                    >
                      {formatTimecode(seg.end)}
                    </div>
                    {/* Source Text */}
                    <div
                      className="px-5 py-4 border-r border-subtle text-muted block leading-relaxed opacity-80 select-text"
                      onClick={(e) => {
                        e.stopPropagation();
                        // Seek video when clicking source text
                        if (videoRef.current) videoRef.current.currentTime = seg.start;
                        setSelectedIndices(new Set([index]));
                      }}
                    >
                      {seg.source_text || "—"}
                    </div>
                    {/* Text Content */}
                    <textarea
                      value={seg.text}
                      onChange={(e) => handleSegmentChange(index, "text", e.target.value)}
                      onFocus={() => {
                        pushHistory();
                        // Seek video to segment start when focusing on text
                        if (videoRef.current) videoRef.current.currentTime = seg.start;
                        setSelectedIndices(new Set([index]));
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="bg-transparent px-5 py-4 resize-none outline-none focus:bg-[rgb(20,20,24)] text-gray-200 min-h-[56px] w-full block leading-relaxed"
                      spellCheck={false}
                      rows={1}
                      onInput={(e) => {
                        e.currentTarget.style.height = "auto";
                        e.currentTarget.style.height = e.currentTarget.scrollHeight + "px";
                      }}
                    />
                  </div>
                );
              })}
              {/* Bottom padding for scroll */}
              <div className="h-40" />
            </div>
          </div>
        </div>

        {/* Right Sidebar - With Accordion & Resizable Copilot */}
        <div className="w-[320px] flex flex-col shrink-0 border-l border-subtle surface-1 overflow-hidden transition-all duration-300">
          {/* Copilot Section - Toggleable Size */}
          <div className={`flex flex-col border-b border-subtle transition-[height] duration-300 ease-in-out ${copilotExpanded ? 'h-[60%] shrink-0' : 'h-[60px] shrink-0'}`}>
            <div className="flex items-center justify-between px-3 py-3 border-b border-subtle bg-surface-2">
              <div className="flex items-center gap-2 text-sm font-medium text-purple-300">
                <div className="w-2 h-2 rounded-full bg-purple-500/50 animate-pulse"></div>
                Omega Copilot
              </div>
              <button
                onClick={() => setCopilotExpanded(!copilotExpanded)}
                className="btn btn-ghost p-1 text-muted hover:text-white"
                title={copilotExpanded ? "Collapse Copilot" : "Expand Copilot"}
              >
                {copilotExpanded ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
              </button>
            </div>
            {/* Copilot Content */}
            {copilotExpanded && (
              <div className="flex-1 min-h-0 overflow-hidden">
                <AssistantPanel jobId={jobId} mode="sidebar" />
              </div>
            )}
          </div>

          {/* Tools Accordion Section */}
          <div className="flex-1 flex flex-col min-h-0 overflow-hidden bg-surface-1">
            <div className="overflow-y-auto flex-1 custom-scrollbar">

              <AccordionItem
                title="Quality & Flagged Items"
                isOpen={activeAccordion === "quality"}
                onToggle={() => setActiveAccordion(activeAccordion === "quality" ? "" : "quality")}
              >
                <AIQualityPanel
                  jobId={jobId}
                  onJumpToSegment={(segmentId) => {
                    const segIndex = segments.findIndex(s => s.id === segmentId);
                    if (segIndex >= 0) {
                      setSelectedIndices(new Set([segIndex]));
                      if (videoRef.current) {
                        videoRef.current.currentTime = segments[segIndex].start;
                      }
                    }
                  }}
                />
              </AccordionItem>

              <AccordionItem
                title="Version History"
                isOpen={activeAccordion === "history"}
                onToggle={() => setActiveAccordion(activeAccordion === "history" ? "" : "history")}
              >
                <VersionHistoryPanel
                  history={history}
                  currentSegments={segments}
                  onRevert={(index) => {
                    if (history[index]) {
                      setSegments(history[index]);
                      setHistory((prev) => prev.slice(0, index));
                      setSelectedIndices(new Set());
                    }
                  }}
                />
              </AccordionItem>

              <AccordionItem
                title="On-Screen Text"
                isOpen={activeAccordion === "ocr"}
                onToggle={() => setActiveAccordion(activeAccordion === "ocr" ? "" : "ocr")}
              >
                <OnScreenTextCapture
                  videoRef={videoRef}
                  onCreateSegment={(timestamp, duration, text) => {
                    pushHistory();
                    const newSegment: Segment = {
                      id: Date.now(),
                      start: timestamp,
                      end: timestamp + duration,
                      text: text,
                    };
                    setSegments((prev) => {
                      const updated = [...prev, newSegment];
                      return updated.sort((a, b) => a.start - b.start);
                    });
                    const newIndex = segments.findIndex((s) => s.start > timestamp);
                    setSelectedIndices(new Set([newIndex >= 0 ? newIndex : segments.length]));
                  }}
                />
              </AccordionItem>

              <AccordionItem
                title="Graphic Zones"
                isOpen={activeAccordion === "zones"}
                onToggle={() => setActiveAccordion(activeAccordion === "zones" ? "" : "zones")}
              >
                <GraphicZonesPanel
                  zones={graphicZones}
                  currentTime={currentTime}
                  onZonesChange={setGraphicZones}
                  onSeekTo={(time) => {
                    if (videoRef.current) {
                      videoRef.current.currentTime = time;
                    }
                  }}
                />
              </AccordionItem>

            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
