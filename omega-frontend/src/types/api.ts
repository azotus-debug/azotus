/**
 * Centralized API types for the Omega frontend.
 *
 * These interfaces mirror the backend API responses and are shared across
 * stores, components, and pages. Import from here instead of redefining
 * locally to keep a single source of truth.
 */

// ---------------------------------------------------------------------------
// Programs store types (store/programs.ts)
// ---------------------------------------------------------------------------

export interface Track {
  id: string;
  program_id: string;
  type: "subtitle" | "dub";
  language_code: string;
  language_name: string;
  stage: string;
  status: string;
  progress: number;
  rating?: number;
  voice_id?: string;
  job_id?: string;
  master_script_id?: string;
  output_path?: string;
  output_version?: number;
  output_override?: boolean;
  override_reason?: string;
  pending_resync?: boolean;
  locked_at?: string;
  locked_by?: string;
  srt_path?: string;
  video_path?: string;
  files_ready?: boolean;
  created_at: string;
  updated_at: string;
}

export interface Program {
  id: string;
  title: string;
  original_filename?: string;
  video_path?: string;
  thumbnail_path?: string;
  duration_seconds?: number;
  client?: string;
  due_date?: string;
  default_style?: string;
  created_at: string;
  updated_at: string;
  tracks: Track[];
  track_completion: string;
  needs_attention: boolean;
}

export interface Delivery {
  id: string;
  track_id: string;
  destination: string;
  recipient?: string;
  delivered_at: string;
  notes?: string;
  program_title?: string;
  language_code?: string;
  track_type?: string;
}

export interface LanguageOption {
  code: string;
  name: string;
  default_mode?: "dub" | "sub";
  default_voice?: string;
}

export interface VoiceOption {
  id: string;
  name: string;
  description?: string;
}

export interface PipelineStatsStage {
  stage: string;
  count: number;
}

export interface PipelineStats {
  total_active: number;
  blocked: number;
  needs_attention: number;
  failed?: number;
  stages?: Record<string, number> | PipelineStatsStage[];
}

export interface AddTrackResult {
  ok: boolean;
  trackId?: string;
  stage?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Ops store types (store/ops.ts)
// ---------------------------------------------------------------------------

export interface OpsSummary {
  total_active: number;
  stage_counts: Record<string, number>;
  bottlenecks: Array<{ stage: string; count: number }>;
  urgent: number;
  overdue: number;
  ready_for_delivery: number;
  ready_for_burn: number;
  awaiting_review: number;
  failed: number;
  timestamp: string;
}

export interface QueueTrack {
  id: string;
  program_id: string;
  program_title: string;
  language_code: string;
  type: string;
  stage: string;
  status: string;
  due_date: string | null;
  client: string | null;
  urgency: "overdue" | "urgent" | "soon" | "normal";
  hours_until_due: number | null;
  output_path?: string;
  srt_path?: string;
  updated_at: string;
}

export interface PendingDelivery {
  id: string;
  program_id: string;
  program_title: string;
  language_code: string;
  type: string;
  stage: string;
  client: string | null;
  due_date: string | null;
  output_path?: string;
}

export interface DeliveryRecord {
  id: string;
  track_id: string;
  program_title: string;
  language_code: string;
  track_type: string;
  client: string | null;
  destination: string;
  delivered_at: string;
}

export interface BatchActionResult {
  track_id: string;
  status: "success" | "skipped" | "error";
  message: string;
}

export interface BatchActionResponse {
  action: string;
  total: number;
  success: number;
  skipped: number;
  errors: number;
  results: BatchActionResult[];
}

// ---------------------------------------------------------------------------
// Subtitle editor types (SubtitleEditor.tsx)
// ---------------------------------------------------------------------------

export interface Segment {
  id?: number | string;
  start: number;
  end: number;
  text: string;
  source_text?: string;
}

export interface TrackInfo {
  language_code?: string;
  meta?: {
    editor_report?: unknown;
    target_language?: string;
    [key: string]: unknown;
  };
}

// ---------------------------------------------------------------------------
// Weekly grid types (WeeklyGridView.tsx / grid/page.tsx)
// ---------------------------------------------------------------------------

export interface GridOutput {
  type: string;
  filename: string;
  status: string;
  size: string;
  url: string;
  error_msg?: string;
}

export interface GridTrack {
  track_id?: string;
  status: string;
  status_label: string;
  outputs_total?: number;
  outputs_ready?: number;
  has_error?: boolean;
  outputs?: GridOutput[];
}

export interface GridProgram {
  program_id: string;
  title: string;
  client_id: string;
  air_date: string;
  thumbnail?: string;
  tracks: Record<string, GridTrack>;
}

export interface GridData {
  week_label: string;
  programs: GridProgram[];
}

// ---------------------------------------------------------------------------
// Graphic zones types (GraphicZonesPanel.tsx)
// ---------------------------------------------------------------------------

export interface GraphicZone {
  id: string;
  startTime: number;
  endTime: number;
  label: string;
  position: "top" | "bottom";
}
