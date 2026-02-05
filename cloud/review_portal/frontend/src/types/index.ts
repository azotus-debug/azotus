export interface Issue {
  id: string;
  segment_index: number;
  type: string;
  message: string;
  severity: "warning" | "error";
  original_text: string;
  current_text: string;
  suggested_text: string;
  timestamp_start: number;
  timestamp_end: number;
  confidence?: number;  // AI confidence score (0-1)
}

export interface ReviewData {
  job_id: string;
  program_name: string;
  language: string;
  total_segments: number;
  auto_approved: number;
  needs_review: number;
  estimated_time: string;
  video_url: string;
  issues: Issue[];
}

export type ReviewAction = "accept" | "edit" | "skip";
