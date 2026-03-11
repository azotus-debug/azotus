"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useToastStore } from "@/store/toast";
import { apiFetch } from "@/lib/api";
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
  Minimize2,
  HelpCircle,
  Search,
  Download,
  Replace,
  CaseSensitive,
  Timer,
  Check,
  X,
  Share2,
  Eye,
  PanelLeftClose,
  PanelLeft,
  Repeat
} from "lucide-react";
import Link from "next/link";
import { AssistantPanel } from "@/components/AssistantPanel";
import { AIQualityPanel } from "@/components/AIQualityPanel";
import { VersionHistoryPanel } from "@/components/VersionHistoryPanel";
import { OnScreenTextCapture } from "@/components/OnScreenTextCapture";
import { GraphicZonesPanel, GraphicZone } from "@/components/GraphicZonesPanel";
import { KeyboardShortcutHelp } from "@/components/common/KeyboardShortcutHelp";

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
  bunnyDirectUrl?: string | null;
  readOnly?: boolean;
  mode?: "edit" | "review";
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

export function SubtitleEditor({ jobId, initialSegments, initialGraphicZones = [], track, bunnyDirectUrl, readOnly = false, mode = "edit" }: SubtitleEditorProps) {
  const addToast = useToastStore(s => s.addToast);
  const [segments, setSegments] = useState<Segment[]>(initialSegments);
  const trackInfo = track ?? null;
  const trackLanguage = (trackInfo?.language_code || trackInfo?.meta?.target_language || 'is').toLowerCase();
  const editorReportRaw = trackInfo?.meta?.editor_report;
  const [saving, setSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const activeSegmentRef = useRef<HTMLDivElement>(null);

  const [history, setHistory] = useState<Segment[][]>(() => {
    try {
      const stored = localStorage.getItem(`omega-history-${jobId}`);
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());
  const [flaggedSegmentIds, setFlaggedSegmentIds] = useState<Set<number | string>>(new Set());
  const [graphicZones, setGraphicZones] = useState<GraphicZone[]>(initialGraphicZones);
  const [isMarkingZone, setIsMarkingZone] = useState(false);
  const [pendingZoneStart, setPendingZoneStart] = useState<number | null>(null);
  const [showOnlyIssues, setShowOnlyIssues] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [showShortcutHelp, setShowShortcutHelp] = useState(false);

  // Find & Replace state
  const [showReplace, setShowReplace] = useState(false);
  const [replaceText, setReplaceText] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);

  // Timing adjust state
  const [showTimingModal, setShowTimingModal] = useState(false);
  const [timingOffset, setTimingOffset] = useState("0");

  // Review mode state
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewComplete, setReviewComplete] = useState(false);
  const [showShareModal, setShowShareModal] = useState(false);
  const [shareLink, setShareLink] = useState("");

  // Sidebar State
  const [activeAccordion, setActiveAccordion] = useState<string>("quality");
  const [copilotExpanded, setCopilotExpanded] = useState(true);

  // Enhanced editor state
  const SPEED_OPTIONS = [1, 1.25, 1.5, 2] as const;
  const [playbackRate, setPlaybackRate] = useState(1);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem("omega-sidebar-collapsed") === "true"; } catch { return false; }
  });
  const VIDEO_SIZES = [60, 50, 35] as const;
  const [videoSizeIndex, setVideoSizeIndex] = useState(1); // default 50%
  const [focusedTextareaIndex, setFocusedTextareaIndex] = useState<number | null>(null);
  const [showGotoModal, setShowGotoModal] = useState(false);
  const [gotoSegmentNum, setGotoSegmentNum] = useState("");
  const [isLoopingSegment, setIsLoopingSegment] = useState(false);
  const minimapRef = useRef<HTMLDivElement>(null);
  const [minimapHover, setMinimapHover] = useState<{ x: number; time: number } | null>(null);

  // AI Editing state
  interface BatchFix { id: number; original: string; suggested: string; reason: string; old_cps: number; new_cps: number; old_longest_line: number; new_longest_line: number; accepted: boolean; }
  interface Alternative { label: string; text: string; cps: number; longest_line: number; }
  const [batchFixLoading, setBatchFixLoading] = useState(false);
  const [showBatchFixModal, setShowBatchFixModal] = useState(false);
  const [batchFixes, setBatchFixes] = useState<BatchFix[]>([]);
  const [alternativesLoading, setAlternativesLoading] = useState(false);
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [alternatives, setAlternatives] = useState<Alternative[]>([]);
  const [alternativesForIndex, setAlternativesForIndex] = useState<number | null>(null);

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
  }, [history.length, initialSegments]);

  // Persist history to localStorage (keep last 20 entries to avoid quota issues)
  useEffect(() => {
    if (history.length > 0) {
      try {
        localStorage.setItem(`omega-history-${jobId}`, JSON.stringify(history.slice(-20)));
      } catch {
        // localStorage full or unavailable - silently ignore
      }
    }
  }, [history, jobId]);

  const pushHistory = () => setHistory((prev) => [...prev.slice(-49), segments]);

  const handleUndo = useCallback(() => {
    if (history.length === 0) return;
    const previous = history[history.length - 1];
    setSegments(previous);
    setHistory((prev) => prev.slice(0, -1));
    setSelectedIndices(new Set());
    setIsDirty(true);
  }, [history]);

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
    let filtered = items;
    if (showOnlyIssues) {
      filtered = filtered.filter((item) => item.warnings.length > 0);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      filtered = filtered.filter((item) =>
        item.segment.text.toLowerCase().includes(q) ||
        (item.segment.source_text || "").toLowerCase().includes(q)
      );
    }
    return filtered;
  }, [segments, warningsByIndex, showOnlyIssues, searchQuery]);

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
      // Allow Cmd/Ctrl+F even when focused on input fields
      if (e.key.toLowerCase() === "f" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        searchInputRef.current?.focus();
        return;
      }
      // Cmd/Ctrl+H: toggle Find & Replace
      if (e.key.toLowerCase() === "h" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setShowReplace(prev => !prev);
        searchInputRef.current?.focus();
        return;
      }

      // Ctrl+B: toggle sidebar (works even in input fields)
      if (e.key.toLowerCase() === "b" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setSidebarCollapsed(prev => !prev);
        return;
      }
      // Ctrl+G: go to segment (works even in input fields)
      if (e.key.toLowerCase() === "g" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setShowGotoModal(prev => !prev);
        return;
      }
      // Ctrl+Shift+V: cycle video size
      if (e.key.toLowerCase() === "v" && (e.metaKey || e.ctrlKey) && e.shiftKey) {
        e.preventDefault();
        setVideoSizeIndex(prev => (prev + 1) % VIDEO_SIZES.length);
        return;
      }

      // Shift+Enter: get AI alternatives (works in textareas)
      if (e.key === "Enter" && e.shiftKey && !e.metaKey && !e.ctrlKey) {
        const target = e.target as HTMLElement;
        if (target.tagName === "TEXTAREA" && focusedTextareaIndex !== null) {
          e.preventDefault();
          handleGetAlternatives(focusedTextareaIndex);
          return;
        }
      }

      // Escape: dismiss alternatives popover
      if (e.key === "Escape" && showAlternatives) {
        e.preventDefault();
        setShowAlternatives(false);
        setAlternativesForIndex(null);
        return;
      }

      // Number keys 1/2/3: select alternative (when popover is open)
      if (showAlternatives && alternatives.length > 0 && ["1", "2", "3"].includes(e.key)) {
        const altIdx = parseInt(e.key) - 1;
        if (altIdx < alternatives.length) {
          e.preventDefault();
          applyAlternative(alternatives[altIdx].text);
          return;
        }
      }

      // Don't trigger other shortcuts when typing in input fields
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
          e.preventDefault();
          togglePlay();
          break;
        case " ": // Space bar
          if (e.shiftKey) {
            e.preventDefault();
            playCurrentSegment();
          } else {
            e.preventDefault();
            togglePlay();
          }
          break;
        case "l":
          e.preventDefault();
          seekNext();
          break;
        case "[":
          e.preventDefault();
          cycleSpeed(-1);
          break;
        case "]":
          e.preventDefault();
          cycleSpeed(1);
          break;
        case "arrowleft":
          e.preventDefault();
          handleSeek(Math.max(0, currentTime - 1/24)); // Frame step back
          break;
        case "arrowright":
          e.preventDefault();
          handleSeek(Math.min(duration, currentTime + 1/24)); // Frame step forward
          break;
        case "home":
          e.preventDefault();
          if (segments.length > 0) {
            handleSeek(segments[0].start);
            setSelectedIndices(new Set([0]));
          }
          break;
        case "end":
          e.preventDefault();
          if (segments.length > 0) {
            const lastIdx = segments.length - 1;
            handleSeek(segments[lastIdx].start);
            setSelectedIndices(new Set([lastIdx]));
          }
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
        case "?":
          e.preventDefault();
          setShowShortcutHelp(prev => !prev);
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments, selectedIndices, focusedTextareaIndex, showAlternatives, alternatives]); // Re-bind when segment/AI state changes

  const togglePlay = useCallback(() => {
    if (videoRef.current) {
      if (videoRef.current.paused) videoRef.current.play();
      else videoRef.current.pause();
    }
  }, []);

  const handleSeek = useCallback((value: number) => {
    if (!videoRef.current) return;
    videoRef.current.currentTime = value;
    setCurrentTime(value);
  }, []);

  const handleSegmentChange = (index: number, field: keyof Segment, value: string | number) => {
    const newSegments = [...segments];
    newSegments[index] = { ...newSegments[index], [field]: value };
    setSegments(newSegments);
    setIsDirty(true);
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
    setIsDirty(true);
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
    setIsDirty(true);
  };

  const handleDelete = () => {
    if (selectedIndices.size === 0) return;
    pushHistory();
    setSegments(segments.filter((_, i) => !selectedIndices.has(i)));
    setSelectedIndices(new Set());
    setIsDirty(true);
  };

  // Timing adjust handler
  const handleTimingAdjust = useCallback(() => {
    if (readOnly) return;
    const offset = parseFloat(timingOffset);
    if (isNaN(offset) || offset === 0) return;
    pushHistory();
    const newSegments = segments.map((seg, i) => {
      if (!selectedIndices.has(i)) return seg;
      return {
        ...seg,
        start: Math.max(0, seg.start + offset),
        end: Math.max(0, seg.end + offset),
      };
    });
    setSegments(newSegments);
    setIsDirty(true);
    setShowTimingModal(false);
    setTimingOffset("0");
    addToast(`Adjusted timing by ${offset > 0 ? "+" : ""}${offset}s for ${selectedIndices.size} segment${selectedIndices.size !== 1 ? "s" : ""}`, "success");
  }, [readOnly, timingOffset, segments, selectedIndices, addToast]);

  // Find & Replace handlers
  const handleReplaceNext = useCallback(() => {
    if (readOnly || !searchQuery || !replaceText) return;
    const compare = caseSensitive ? (s: string) => s : (s: string) => s.toLowerCase();
    const query = compare(searchQuery);
    const newSegments = [...segments];
    for (let i = 0; i < newSegments.length; i++) {
      const idx = compare(newSegments[i].text).indexOf(query);
      if (idx !== -1) {
        pushHistory();
        const original = newSegments[i].text;
        newSegments[i] = {
          ...newSegments[i],
          text: original.substring(0, idx) + replaceText + original.substring(idx + searchQuery.length),
        };
        setSegments(newSegments);
        setIsDirty(true);
        return;
      }
    }
  }, [readOnly, searchQuery, replaceText, caseSensitive, segments]);

  const handleReplaceAll = useCallback(() => {
    if (readOnly || !searchQuery || !replaceText) return;
    pushHistory();
    const flags = caseSensitive ? "g" : "gi";
    const regex = new RegExp(searchQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
    let replaced = 0;
    const newSegments = segments.map(seg => {
      const newText = seg.text.replace(regex, replaceText);
      if (newText !== seg.text) {
        replaced++;
        return { ...seg, text: newText };
      }
      return seg;
    });
    if (replaced > 0) {
      setSegments(newSegments);
      setIsDirty(true);
      addToast(`Replaced in ${replaced} segment${replaced !== 1 ? "s" : ""}`, "success");
    } else {
      addToast("No matches found", "info");
    }
  }, [readOnly, searchQuery, replaceText, caseSensitive, segments, addToast]);

  // Share for review handler
  const handleGenerateShareLink = useCallback(async () => {
    try {
      const res = await apiFetch("/api/auth/review-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId }),
      });
      if (!res.ok) throw new Error("Failed to generate link");
      const data = await res.json();
      setShareLink(data.review_url);
      setShowShareModal(true);
    } catch {
      addToast("Failed to generate review link", "error");
    }
  }, [jobId, addToast]);

  // Review approve/reject handlers
  const handleReviewAction = useCallback(async (action: "approve" | "reject") => {
    setReviewSubmitting(true);
    try {
      const res = await apiFetch(`/api/editor/${jobId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comments: "" }),
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      setReviewComplete(true);
      addToast(action === "approve" ? "Approved successfully" : "Changes requested", "success");
    } catch {
      addToast(`${action} failed`, "error");
    } finally {
      setReviewSubmitting(false);
    }
  }, [jobId, addToast]);

  const handleSave = useCallback(async () => {
    if (readOnly) return;
    setSaving(true);
    try {
      await apiFetch(`/api/editor/${jobId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          segments,
          graphic_zones: graphicZones,
          history: history
        }),
      });
      setLastSaved(new Date());
      setIsDirty(false);
      addToast("Saved successfully", "success");
    } catch {
      addToast("Save failed", "error");
    } finally {
      setSaving(false);
    }
  }, [addToast, graphicZones, history, jobId, segments]);

  const activeIndex = segments.findIndex((s) => currentTime >= s.start && currentTime < s.end);
  const activeSegment = activeIndex >= 0 ? segments[activeIndex] : null;

  useEffect(() => {
    if (activeIndex !== -1 && activeSegmentRef.current && !selectedIndices.has(activeIndex)) {
      activeSegmentRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeIndex, selectedIndices]);

  // Auto-save every 30s when dirty
  useEffect(() => {
    if (!isDirty) return;
    const timer = setTimeout(() => {
      handleSave();
    }, 30000);
    return () => clearTimeout(timer);
  }, [isDirty, handleSave]);

  // Warn before leaving with unsaved changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  // Apply playback rate to video
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate;
  }, [playbackRate]);

  // Persist sidebar state
  useEffect(() => {
    try { localStorage.setItem("omega-sidebar-collapsed", String(sidebarCollapsed)); } catch {}
  }, [sidebarCollapsed]);

  // Segment loop: pause at OUT timecode when looping
  useEffect(() => {
    if (!isLoopingSegment || !videoRef.current) return;
    const video = videoRef.current;
    const checkLoop = () => {
      if (activeIndex >= 0 && video.currentTime >= segments[activeIndex].end) {
        video.pause();
        setIsLoopingSegment(false);
      }
    };
    video.addEventListener("timeupdate", checkLoop);
    return () => video.removeEventListener("timeupdate", checkLoop);
  }, [isLoopingSegment, activeIndex, segments]);

  // Cycle playback speed
  const cycleSpeed = useCallback((direction: 1 | -1) => {
    setPlaybackRate(prev => {
      const idx = SPEED_OPTIONS.indexOf(prev as typeof SPEED_OPTIONS[number]);
      const next = idx + direction;
      if (next < 0) return SPEED_OPTIONS[SPEED_OPTIONS.length - 1];
      if (next >= SPEED_OPTIONS.length) return SPEED_OPTIONS[0];
      return SPEED_OPTIONS[next];
    });
  }, []);

  // Play current segment only (Shift+Space)
  const playCurrentSegment = useCallback(() => {
    if (!videoRef.current || activeIndex < 0) return;
    const seg = segments[activeIndex];
    videoRef.current.currentTime = seg.start;
    videoRef.current.play();
    setIsLoopingSegment(true);
  }, [activeIndex, segments]);

  // Go to segment by number
  const handleGotoSegment = useCallback(() => {
    const num = parseInt(gotoSegmentNum, 10);
    if (isNaN(num) || num < 1 || num > segments.length) return;
    const targetIndex = num - 1;
    setSelectedIndices(new Set([targetIndex]));
    if (videoRef.current) videoRef.current.currentTime = segments[targetIndex].start;
    setShowGotoModal(false);
    setGotoSegmentNum("");
  }, [gotoSegmentNum, segments]);

  // AI: Batch QC Fix
  const handleBatchFix = useCallback(async () => {
    setBatchFixLoading(true);
    try {
      const res = await apiFetch(`/api/editor/${jobId}/ai/batch-fix`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments }),
      });
      if (!res.ok) throw new Error("API error");
      const data = await res.json();
      const fixes = (data.fixes || []).map((f: Omit<BatchFix, 'accepted'>) => ({ ...f, accepted: true }));
      if (fixes.length === 0) {
        addToast("No QC issues to fix", "info");
      } else {
        setBatchFixes(fixes);
        setShowBatchFixModal(true);
      }
    } catch {
      addToast("AI batch fix failed", "error");
    } finally {
      setBatchFixLoading(false);
    }
  }, [jobId, segments, addToast]);

  const applyBatchFixes = useCallback(() => {
    const accepted = batchFixes.filter(f => f.accepted);
    if (accepted.length === 0) {
      setShowBatchFixModal(false);
      return;
    }
    pushHistory();
    const fixMap = new Map(accepted.map(f => [f.id, f.suggested]));
    const newSegments = segments.map(seg => {
      if (seg.id !== undefined && fixMap.has(seg.id)) {
        return { ...seg, text: fixMap.get(seg.id)! };
      }
      return seg;
    });
    setSegments(newSegments);
    setIsDirty(true);
    setShowBatchFixModal(false);
    addToast(`Applied ${accepted.length} AI fixes`, "success");
  }, [batchFixes, segments, addToast]);

  // AI: Get Alternatives
  const handleGetAlternatives = useCallback(async (index: number) => {
    const seg = segments[index];
    if (!seg) return;
    setAlternativesLoading(true);
    setAlternativesForIndex(index);
    setShowAlternatives(true);
    setAlternatives([]);
    try {
      const contextBefore = segments.slice(Math.max(0, index - 3), index);
      const contextAfter = segments.slice(index + 1, index + 4);
      const res = await apiFetch(`/api/editor/${jobId}/ai/alternatives`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segment: seg, context_before: contextBefore, context_after: contextAfter }),
      });
      if (!res.ok) throw new Error("API error");
      const data = await res.json();
      setAlternatives(data.alternatives || []);
    } catch {
      addToast("Failed to get alternatives", "error");
      setShowAlternatives(false);
    } finally {
      setAlternativesLoading(false);
    }
  }, [jobId, segments, addToast]);

  const applyAlternative = useCallback((altText: string) => {
    if (alternativesForIndex === null) return;
    pushHistory();
    const newSegments = [...segments];
    newSegments[alternativesForIndex] = { ...newSegments[alternativesForIndex], text: altText };
    setSegments(newSegments);
    setIsDirty(true);
    setShowAlternatives(false);
    setAlternativesForIndex(null);
  }, [alternativesForIndex, segments]);

  const seekPrev = useCallback(() => {
    if (!segments.length) return;
    const targetIndex = Math.max(0, activeIndex > 0 ? activeIndex - 1 : 0);
    const target = segments[targetIndex];
    handleSeek(target.start);
    setSelectedIndices(new Set([targetIndex]));
  }, [activeIndex, handleSeek, segments]);

  const seekNext = useCallback(() => {
    if (!segments.length) return;
    const targetIndex = Math.min(segments.length - 1, activeIndex >= 0 ? activeIndex + 1 : 0);
    const target = segments[targetIndex];
    handleSeek(target.start);
    setSelectedIndices(new Set([targetIndex]));
  }, [activeIndex, handleSeek, segments]);

  return (
    <div className="flex h-screen w-screen flex-col bg-[rgb(10,10,12)] overflow-hidden">
      {/* Header */}
      <header className="h-12 flex items-center justify-between px-4 border-b border-subtle surface-1 shrink-0">
        <div className="flex items-center gap-4">
          <Link href="/" className="btn btn-ghost p-2">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <div className="label flex items-center gap-2">
              {mode === "review" ? "Review" : "Workstation"}
              {mode === "review" && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] bg-purple-600/20 text-purple-300">
                  <Eye className="w-2.5 h-2.5" /> Read-only
                </span>
              )}
            </div>
            <div className="text-sm font-medium text-primary truncate max-w-[200px]">{jobId}</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {isDirty ? (
            <span className="pill text-[10px] text-amber-400">
              <span className="w-2 h-2 rounded-full bg-amber-400 inline-block animate-pulse" />
              Unsaved changes
            </span>
          ) : lastSaved ? (
            <span className="pill text-[10px]">
              <Clock className="w-3 h-3" />
              Saved {lastSaved.toLocaleTimeString()}
            </span>
          ) : null}
          <button
            onClick={() => setShowShortcutHelp(true)}
            className="btn btn-ghost p-2 text-muted hover:text-white"
            title="Keyboard shortcuts (?)"
          >
            <HelpCircle className="w-4 h-4" />
          </button>
          {/* Share for Review (edit mode only) */}
          {mode === "edit" && (
            <button
              onClick={handleGenerateShareLink}
              className="btn btn-ghost text-xs text-muted hover:text-white flex items-center gap-1"
              title="Generate review link"
            >
              <Share2 className="w-3.5 h-3.5" />
              Share
            </button>
          )}
          {/* Export Dropdown */}
          <div className="relative group">
            <button className="btn btn-ghost text-xs text-muted hover:text-white flex items-center gap-1">
              <Download className="w-3.5 h-3.5" />
              Export
              <ChevronDown className="w-3 h-3" />
            </button>
            <div className="absolute right-0 top-full mt-1 hidden group-hover:block bg-[rgb(30,30,30)] border border-subtle rounded-lg shadow-xl py-1 z-50 min-w-[120px]">
              {["SRT", "VTT", "TTML"].map(fmt => (
                <a
                  key={fmt}
                  href={`/api/editor/${jobId}/export/${fmt.toLowerCase()}`}
                  download
                  className="block px-4 py-1.5 text-xs text-gray-300 hover:bg-white/5 hover:text-white transition-colors"
                >
                  {fmt}
                </a>
              ))}
            </div>
          </div>
          {/* Timing Adjust */}
          <button
            onClick={() => setShowTimingModal(true)}
            disabled={selectedIndices.size === 0 || readOnly}
            className="btn btn-ghost text-xs text-muted hover:text-white disabled:opacity-40 flex items-center gap-1"
            title="Adjust timing of selected segments"
          >
            <Timer className="w-3.5 h-3.5" />
            Timing
          </button>
          {mode === "review" ? (
            reviewComplete ? (
              <span className="text-xs text-emerald-400 flex items-center gap-1">
                <Check className="w-4 h-4" /> Review submitted
              </span>
            ) : (
              <>
                <button
                  onClick={() => handleReviewAction("reject")}
                  disabled={reviewSubmitting}
                  className="btn btn-ghost text-xs text-rose-400 hover:text-rose-300 flex items-center gap-1"
                >
                  <X className="w-4 h-4" /> Request Changes
                </button>
                <button
                  onClick={() => handleReviewAction("approve")}
                  disabled={reviewSubmitting}
                  className="btn text-xs bg-emerald-600/30 text-emerald-300 hover:bg-emerald-600/50 flex items-center gap-1 px-4 py-1.5 rounded-lg"
                >
                  {reviewSubmitting ? <Loader2 className="w-4 h-4 spin" /> : <Check className="w-4 h-4" />}
                  Approve
                </button>
              </>
            )
          ) : (
            <button onClick={handleSave} disabled={saving || readOnly} className="btn btn-primary">
              {saving ? <Loader2 className="w-4 h-4 spin" /> : <Save className="w-4 h-4" />}
              Save
            </button>
          )}
        </div>
      </header>

      {/* Main Layout: Video + Segments + Right Sidebar */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Left: Video + Transport + Segment List - with left padding for edge spacing */}
        <div className="flex-1 flex flex-col min-w-0 pl-6 overflow-hidden">
          {/* Video Container - Dynamic height with toggle */}
          <div className={`min-h-[200px] flex flex-col bg-black shrink-0 transition-[height] duration-300 ease-in-out`} style={{ height: `${VIDEO_SIZES[videoSizeIndex]}%` }}>
            <div className="flex-1 relative flex items-center justify-center min-h-0 overflow-hidden">
              <video
                ref={videoRef}
                src={`/api/stream/${jobId}`}
                className="max-w-full max-h-full object-contain rounded-sm"
                onClick={togglePlay}
                onError={(e) => {
                  const vid = e.currentTarget;
                  // Try Bunny CDN fallback before showing error
                  if (bunnyDirectUrl && !vid.dataset.triedCdn) {
                    vid.dataset.triedCdn = "1";
                    vid.src = bunnyDirectUrl;
                    return;
                  }
                  vid.style.display = 'none';
                  const p = vid.parentElement;
                  if (p && !p.querySelector('.video-fallback')) {
                    const fb = document.createElement('div');
                    fb.className = 'video-fallback absolute inset-0 flex flex-col items-center justify-center text-gray-500 bg-black/50 text-xs px-4 text-center';
                    fb.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mb-3 opacity-50"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/></svg>Video preview not available or processing';
                    p.appendChild(fb);
                  }
                }}
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

            {/* Transport Controls — Enhanced */}
            <div className="px-4 py-2 border-t border-subtle surface-1 shrink-0">
              <div className="flex items-center justify-between gap-4 mb-1.5">
                <span className="font-mono text-cyan text-sm tracking-wide w-28">{formatTimecode(currentTime)}</span>
                <div className="flex items-center gap-1">
                  <button onClick={seekPrev} className="btn btn-ghost p-2" title="Previous segment (J)">
                    <SkipBack className="w-4 h-4" />
                  </button>
                  <button onClick={togglePlay} className="btn btn-primary p-3 rounded-full" title="Play/Pause (K)">
                    {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                  </button>
                  <button onClick={seekNext} className="btn btn-ghost p-2" title="Next segment (L)">
                    <SkipForward className="w-4 h-4" />
                  </button>
                  <button
                    onClick={playCurrentSegment}
                    className={`btn btn-ghost p-2 ${isLoopingSegment ? "text-cyan-400" : ""}`}
                    title="Play current segment (Shift+Space)"
                  >
                    <Repeat className="w-4 h-4" />
                  </button>
                  <div className="w-px h-4 bg-[rgba(255,255,255,0.08)] mx-1" />
                  <button
                    onClick={() => cycleSpeed(1)}
                    className={`speed-btn ${playbackRate !== 1 ? "active" : ""}`}
                    title="Playback speed ([ / ])"
                  >
                    {playbackRate}x
                  </button>
                </div>
                <span className="font-mono text-muted text-xs w-28 text-right">{formatTimecode(duration)}</span>
              </div>
              {/* Segment Mini-Map Seek Bar */}
              <div
                ref={minimapRef}
                className="segment-minimap"
                onClick={(e) => {
                  if (!minimapRef.current || !duration) return;
                  const rect = minimapRef.current.getBoundingClientRect();
                  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  handleSeek(pct * duration);
                }}
                onMouseMove={(e) => {
                  if (!minimapRef.current || !duration) return;
                  const rect = minimapRef.current.getBoundingClientRect();
                  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                  setMinimapHover({ x: e.clientX - rect.left, time: pct * duration });
                }}
                onMouseLeave={() => setMinimapHover(null)}
              >
                {/* Segment markers */}
                {duration > 0 && segments.map((seg, i) => {
                  const left = (seg.start / duration) * 100;
                  const width = Math.max(0.15, ((seg.end - seg.start) / duration) * 100);
                  return (
                    <div
                      key={i}
                      className={`segment-minimap-marker ${i === activeIndex ? "segment-minimap-active" : ""}`}
                      style={{ left: `${left}%`, width: `${width}%` }}
                    />
                  );
                })}
                {/* Playhead */}
                {duration > 0 && (
                  <div
                    className="segment-minimap-progress"
                    style={{ left: `${(currentTime / duration) * 100}%` }}
                  />
                )}
                {/* Hover tooltip */}
                {minimapHover && (
                  <div className="segment-minimap-hover" style={{ left: minimapHover.x }}>
                    {formatTimecode(minimapHover.time)}
                  </div>
                )}
              </div>
              {/* Segment Info Line */}
              {activeIndex >= 0 && (
                <div className="segment-info-line">
                  <span><span className="info-label">Seg</span> {activeIndex + 1}/{segments.length}</span>
                  <span><span className="info-label">CPS</span> {(() => {
                    const seg = segments[activeIndex];
                    const dur = seg.end - seg.start;
                    const chars = (seg.text || "").replace(/\s+/g, " ").trim().length;
                    return dur > 0 ? (chars / dur).toFixed(1) : "—";
                  })()}</span>
                  <span><span className="info-label">Dur</span> {(segments[activeIndex].end - segments[activeIndex].start).toFixed(2)}s</span>
                </div>
              )}
            </div>
          </div>
          {/* Video/Segments Split Toggle */}
          <div
            className="video-size-toggle"
            onClick={() => setVideoSizeIndex(prev => (prev + 1) % VIDEO_SIZES.length)}
            title={`Video: ${VIDEO_SIZES[videoSizeIndex]}% (Ctrl+Shift+V to cycle)`}
          />

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

            {/* Search + Replace Bar */}
            <div className="border-b border-subtle shrink-0">
              <div className="h-9 flex items-center gap-2 px-5">
                <Search className="w-3.5 h-3.5 text-muted shrink-0" />
                <input
                  ref={searchInputRef}
                  type="text"
                  placeholder="Search segments..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="flex-1 bg-transparent text-sm text-gray-200 outline-none placeholder:text-gray-600"
                />
                <button
                  onClick={() => setCaseSensitive(prev => !prev)}
                  className={`p-1 rounded transition-colors ${caseSensitive ? "text-cyan-400 bg-cyan-500/10" : "text-gray-600 hover:text-gray-400"}`}
                  title="Case sensitive"
                >
                  <CaseSensitive className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setShowReplace(prev => !prev)}
                  className={`p-1 rounded transition-colors ${showReplace ? "text-cyan-400 bg-cyan-500/10" : "text-gray-600 hover:text-gray-400"}`}
                  title="Find & Replace (Cmd+H)"
                >
                  <Replace className="w-3.5 h-3.5" />
                </button>
                {searchQuery && (
                  <button
                    onClick={() => setSearchQuery("")}
                    className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    Clear
                  </button>
                )}
                {searchQuery && (
                  <span className="text-xs text-muted">
                    {visibleSegments.length} result{visibleSegments.length !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
              {showReplace && (
                <div className="h-9 flex items-center gap-2 px-5 border-t border-subtle">
                  <Replace className="w-3.5 h-3.5 text-muted shrink-0" />
                  <input
                    type="text"
                    placeholder="Replace with..."
                    value={replaceText}
                    onChange={(e) => setReplaceText(e.target.value)}
                    className="flex-1 bg-transparent text-sm text-gray-200 outline-none placeholder:text-gray-600"
                  />
                  <button
                    onClick={handleReplaceNext}
                    disabled={!searchQuery || !replaceText || readOnly}
                    className="text-xs px-2 py-0.5 rounded bg-cyan-600/20 text-cyan-300 hover:bg-cyan-600/40 disabled:opacity-40 transition-colors"
                  >
                    Replace
                  </button>
                  <button
                    onClick={handleReplaceAll}
                    disabled={!searchQuery || !replaceText || readOnly}
                    className="text-xs px-2 py-0.5 rounded bg-cyan-600/20 text-cyan-300 hover:bg-cyan-600/40 disabled:opacity-40 transition-colors"
                  >
                    Replace All
                  </button>
                </div>
              )}
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
              <div className="flex items-center gap-2">
                {!readOnly && warningSummary.segmentsWithIssues > 0 && (
                  <button
                    type="button"
                    className="qc-fix-btn"
                    onClick={handleBatchFix}
                    disabled={batchFixLoading}
                    title="AI fix all QC violations"
                  >
                    {batchFixLoading ? <span className="spinner" /> : <span>&#10024;</span>}
                    {batchFixLoading ? "Fixing..." : "Fix All"}
                  </button>
                )}
                <button
                  type="button"
                  className={`qc-toggle${showOnlyIssues ? " active" : ""}`}
                  onClick={() => setShowOnlyIssues((prev) => !prev)}
                  disabled={warningSummary.segmentsWithIssues === 0}
                >
                  Show only issues
                </button>
              </div>
            </div>

            {/* Segment List - Scrollable */}
            <div className="flex-1 overflow-y-auto min-h-0">
              {/* Header Row - Professional spacing with row number */}
              <div className="grid grid-cols-[40px_120px_120px_1fr_1fr] border-b border-subtle sticky top-0 z-10 surface-2 text-[11px] text-muted font-medium uppercase tracking-wider shadow-sm">
                <div className="px-1 py-4 border-r border-subtle text-center">#</div>
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
                // Char counter calc
                const segText = seg.text || "";
                const segLines = segText.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
                const longestLine = segLines.length ? Math.max(...segLines.map(l => l.length)) : segText.length;
                const segDur = seg.end - seg.start;
                const segChars = segText.replace(/\s+/g, " ").trim().length;
                const segCps = segDur > 0 ? segChars / segDur : 0;
                const { ideal: cpsIdeal, tight: cpsTight } = getCpsTargets(trackLanguage);
                const charStatus = segCps > cpsTight ? "error" : segCps > cpsIdeal ? "warn" : "ok";
                return (
                  <div
                    key={index}
                    ref={isActive ? activeSegmentRef : null}
                    onClick={(e) => {
                      handleSelect(index, e.metaKey || e.ctrlKey || e.shiftKey);
                      if (!isSelected && videoRef.current) videoRef.current.currentTime = seg.start;
                    }}
                    className={`grid grid-cols-[40px_120px_120px_1fr_1fr] border-b text-sm cursor-pointer transition-all duration-75 ease-out ${warnings.length > 0 ? (warningLevel === "error" ? "segment-row--error" : "segment-row--warn") : ""} ${isFlagged ? "border-l-2 border-l-amber-500 bg-amber-500/5" : ""
                      } ${isSelected
                        ? "bg-cyan-500/10 border-l-2 border-l-cyan-500"
                        : isActive
                          ? "segment-active-glow"
                          : "hover:bg-white/[0.02]"
                      } border-subtle`}
                    title={isFlagged ? "\u26A0\uFE0F Flagged for review" : undefined}
                  >
                    {/* Row Number */}
                    <div className="segment-row-number px-1 py-4 border-r border-subtle flex items-center justify-center">
                      {index + 1}
                    </div>
                    {/* IN Timecode */}
                    <div
                      className="px-5 py-4 border-r border-subtle font-mono text-emerald-400 cursor-text flex items-center gap-2 tracking-wider text-[13px]"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (videoRef.current) videoRef.current.currentTime = seg.start;
                        setSelectedIndices(new Set([index]));
                      }}
                    >
                      {formatTimecode(seg.start)}
                      {warnings.length > 0 && (
                        <span
                          className={`qc-warning qc-warning--${warningLevel}`}
                          title={warnings.map((w) => w.msg).join(" \u2022 ")}
                        >
                          {"\u26A0\uFE0F"}
                        </span>
                      )}
                    </div>
                    {/* OUT Timecode */}
                    <div
                      className="px-5 py-4 border-r border-subtle font-mono text-rose-400 cursor-text flex items-center tracking-wider text-[13px]"
                      onClick={(e) => {
                        e.stopPropagation();
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
                        if (videoRef.current) videoRef.current.currentTime = seg.start;
                        setSelectedIndices(new Set([index]));
                      }}
                    >
                      {seg.source_text || "\u2014"}
                    </div>
                    {/* Text Content with Character Counter */}
                    <div className="relative">
                      <textarea
                        value={seg.text}
                        onChange={(e) => handleSegmentChange(index, "text", e.target.value)}
                        onFocus={() => {
                          pushHistory();
                          if (videoRef.current) videoRef.current.currentTime = seg.start;
                          setSelectedIndices(new Set([index]));
                          setFocusedTextareaIndex(index);
                        }}
                        onBlur={() => setFocusedTextareaIndex(null)}
                        onClick={(e) => e.stopPropagation()}
                        className="bg-transparent px-5 py-4 resize-none outline-none focus:bg-[rgb(20,20,24)] text-gray-200 min-h-[56px] w-full block leading-relaxed"
                        spellCheck={false}
                        rows={1}
                        onInput={(e) => {
                          e.currentTarget.style.height = "auto";
                          e.currentTarget.style.height = e.currentTarget.scrollHeight + "px";
                        }}
                      />
                      {/* Character Counter */}
                      <div className={`char-counter char-counter--${charStatus} ${focusedTextareaIndex === index ? "visible" : ""}`}>
                        {longestLine}/{MAX_CHARS_PER_LINE} · {segCps.toFixed(1)}
                      </div>
                      {/* AI Alternatives Popover */}
                      {showAlternatives && alternativesForIndex === index && (
                        <div className="alternatives-popover" onClick={e => e.stopPropagation()}>
                          <div className="alternatives-header">
                            <span>AI Alternatives</span>
                            <button
                              onClick={() => { setShowAlternatives(false); setAlternativesForIndex(null); }}
                              className="text-gray-500 hover:text-gray-300 text-xs"
                            >
                              Esc
                            </button>
                          </div>
                          {alternativesLoading ? (
                            <div className="alternatives-loading">
                              <span className="spinner" />
                              Generating alternatives...
                            </div>
                          ) : alternatives.length === 0 ? (
                            <div className="alternatives-loading">No alternatives available</div>
                          ) : (
                            alternatives.map((alt, altIdx) => {
                              const altCpsClass = alt.cps > cpsTight ? "error" : alt.cps > cpsIdeal ? "warn" : "ok";
                              return (
                                <div
                                  key={altIdx}
                                  className="alternative-option"
                                  onClick={() => applyAlternative(alt.text)}
                                >
                                  <span className="alternative-key">{altIdx + 1}</span>
                                  <div className="alternative-content">
                                    <div className="alternative-label">{alt.label}</div>
                                    <div className="alternative-text">{alt.text}</div>
                                  </div>
                                  <div className="alternative-meta">
                                    <span className={`alternative-cps alternative-cps--${altCpsClass}`}>
                                      {alt.cps.toFixed(1)}
                                    </span>
                                  </div>
                                </div>
                              );
                            })
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {/* Bottom padding for scroll */}
              <div className="h-40" />
            </div>
          </div>
        </div>

        {/* Right Sidebar - Collapsible */}
        <div
          className="relative flex flex-col shrink-0 border-l border-subtle surface-1 overflow-hidden transition-all duration-300"
          style={{ width: sidebarCollapsed ? 0 : 320, borderLeftWidth: sidebarCollapsed ? 0 : undefined }}
        >
          {/* Sidebar Toggle */}
          <button
            className="sidebar-toggle"
            onClick={() => setSidebarCollapsed(prev => !prev)}
            title={sidebarCollapsed ? "Show sidebar (Ctrl+B)" : "Hide sidebar (Ctrl+B)"}
          >
            {sidebarCollapsed ? <PanelLeft className="w-3 h-3" /> : <PanelLeftClose className="w-3 h-3" />}
          </button>
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
                <AssistantPanel
                  jobId={jobId}
                  mode="sidebar"
                  onApplySuggestion={(segmentId, newText) => {
                    const idx = segments.findIndex(s => s.id === segmentId);
                    if (idx >= 0) {
                      pushHistory();
                      const newSegments = [...segments];
                      newSegments[idx] = { ...newSegments[idx], text: newText };
                      setSegments(newSegments);
                      setSelectedIndices(new Set([idx]));
                      setIsDirty(true);
                    }
                  }}
                />
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
                  editorReport={editorReportRaw}
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

      <KeyboardShortcutHelp
        open={showShortcutHelp}
        onClose={() => setShowShortcutHelp(false)}
      />

      {/* Share Review Link Modal */}
      {showShareModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowShareModal(false)}>
          <div className="bg-[rgb(30,30,30)] border border-subtle rounded-xl p-6 w-96 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-gray-200 mb-2">Share for Review</h3>
            <p className="text-xs text-gray-500 mb-4">Send this link to a reviewer. It expires in 72 hours.</p>
            <div className="flex items-center gap-2 mb-4">
              <input
                readOnly
                value={shareLink}
                className="flex-1 px-2 py-1.5 rounded bg-[rgb(20,20,20)] border border-subtle text-xs text-gray-300 outline-none font-mono"
                onClick={e => (e.target as HTMLInputElement).select()}
              />
              <button
                onClick={() => {
                  navigator.clipboard.writeText(shareLink);
                  addToast("Link copied to clipboard", "success");
                }}
                className="text-xs px-3 py-1.5 rounded bg-cyan-600/30 text-cyan-300 hover:bg-cyan-600/50 transition-colors shrink-0"
              >
                Copy
              </button>
            </div>
            <div className="flex justify-end">
              <button onClick={() => setShowShareModal(false)} className="text-xs px-3 py-1.5 rounded text-gray-400 hover:text-gray-200 transition-colors">
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Go-to-Segment Modal */}
      {showGotoModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => { setShowGotoModal(false); setGotoSegmentNum(""); }}>
          <div className="goto-modal" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-gray-200 mb-3">Go to Segment</h3>
            <input
              type="number"
              min={1}
              max={segments.length}
              placeholder={`1 – ${segments.length}`}
              value={gotoSegmentNum}
              onChange={e => setGotoSegmentNum(e.target.value)}
              autoFocus
              onKeyDown={e => {
                if (e.key === "Enter") handleGotoSegment();
                if (e.key === "Escape") { setShowGotoModal(false); setGotoSegmentNum(""); }
              }}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => { setShowGotoModal(false); setGotoSegmentNum(""); }} className="text-xs px-3 py-1.5 rounded text-gray-400 hover:text-gray-200 transition-colors">
                Cancel
              </button>
              <button onClick={handleGotoSegment} className="text-xs px-3 py-1.5 rounded bg-cyan-600/30 text-cyan-300 hover:bg-cyan-600/50 transition-colors">
                Go
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Batch QC Fix Modal */}
      {showBatchFixModal && (
        <div className="batch-fix-overlay" onClick={() => setShowBatchFixModal(false)}>
          <div className="batch-fix-modal" onClick={e => e.stopPropagation()}>
            <div className="batch-fix-header">
              <h3>AI QC Fixes ({batchFixes.length} suggestions)</h3>
              <button
                onClick={() => setShowBatchFixModal(false)}
                className="text-gray-500 hover:text-gray-300 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="batch-fix-body">
              {batchFixes.map((fix, i) => (
                <div key={fix.id} className="fix-card">
                  <div className="fix-card-header">
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={fix.accepted}
                        onChange={() => {
                          setBatchFixes(prev => prev.map((f, j) =>
                            j === i ? { ...f, accepted: !f.accepted } : f
                          ));
                        }}
                        className="accent-purple-500"
                      />
                      <span className="fix-card-id">Segment #{fix.id}</span>
                    </div>
                    <span className="fix-reason-badge">{fix.reason}</span>
                  </div>
                  <div className="fix-diff">
                    <div className="fix-diff-original">{fix.original}</div>
                    <div className="fix-diff-suggested">{fix.suggested}</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="batch-fix-footer">
              <div className="text-xs text-gray-500">
                {batchFixes.filter(f => f.accepted).length} of {batchFixes.length} selected
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowBatchFixModal(false)}
                  className="text-xs px-3 py-1.5 rounded text-gray-400 hover:text-gray-200 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={applyBatchFixes}
                  className="text-xs px-4 py-1.5 rounded bg-purple-600/30 text-purple-300 hover:bg-purple-600/50 transition-colors font-medium"
                >
                  Apply Selected
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Timing Adjust Modal */}
      {showTimingModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowTimingModal(false)}>
          <div className="bg-[rgb(30,30,30)] border border-subtle rounded-xl p-6 w-80 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-gray-200 mb-4">Adjust Timing</h3>
            <p className="text-xs text-gray-500 mb-3">{selectedIndices.size} segment{selectedIndices.size !== 1 ? "s" : ""} selected</p>
            <div className="flex items-center gap-2 mb-4">
              <label className="text-xs text-gray-400">Offset (seconds):</label>
              <input
                type="number"
                step="0.1"
                value={timingOffset}
                onChange={e => setTimingOffset(e.target.value)}
                className="w-24 px-2 py-1.5 rounded bg-[rgb(20,20,20)] border border-subtle text-sm text-gray-200 outline-none"
                autoFocus
                onKeyDown={e => { if (e.key === "Enter") handleTimingAdjust(); }}
              />
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowTimingModal(false)} className="text-xs px-3 py-1.5 rounded text-gray-400 hover:text-gray-200 transition-colors">
                Cancel
              </button>
              <button onClick={handleTimingAdjust} className="text-xs px-3 py-1.5 rounded bg-cyan-600/30 text-cyan-300 hover:bg-cyan-600/50 transition-colors">
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
