"use client";

import { useState, useRef, useCallback } from "react";
import { Upload, X, Film, FileText, Loader2, Check, Zap, ShieldAlert, Languages, Flame } from "lucide-react";
import { getStoredToken, getBackendDirectUrl } from "@/lib/api";
const VIDEO_EXTENSIONS = /\.(mp4|mov|mkv|avi|webm|m4v|wmv|flv|mts|m2ts|ts|mxf|prores)$/i;

interface ImportMediaModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

type PipelineMode = "full_pipeline" | "quick_burn" | "skip_transcription";

interface LanguageOption {
  code: string;
  name: string;
  flag: string;
}

const AVAILABLE_LANGUAGES: LanguageOption[] = [
  { code: "is", name: "Icelandic", flag: "🇮🇸" },
  { code: "nl", name: "Dutch", flag: "🇳🇱" },
  { code: "es", name: "Spanish", flag: "🇪🇸" },
  { code: "pt", name: "Portuguese", flag: "🇵🇹" },
  { code: "de", name: "German", flag: "🇩🇪" },
  { code: "fr", name: "French", flag: "🇫🇷" },
];

export default function ImportMediaModal({ isOpen, onClose, onSuccess }: ImportMediaModalProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [srtFile, setSrtFile] = useState<File | null>(null);
  const [selectedLanguages, setSelectedLanguages] = useState<string[]>(["is"]);
  const [pipelineMode, setPipelineMode] = useState<PipelineMode>("full_pipeline");
  const [reviewMode, setReviewMode] = useState<"automatic" | "human">("automatic");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const srtInputRef = useRef<HTMLInputElement>(null);

  // "translate" = English SRT → translate to target language
  // "burn" = Already-translated SRT → burn directly onto video
  const [srtMode, setSrtMode] = useState<"translate" | "burn">("burn");

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    const videoFile = files.find(f => VIDEO_EXTENSIONS.test(f.name));
    const srt = files.find(f => /\.srt$/i.test(f.name));

    if (videoFile) setSelectedFile(videoFile);
    if (srt) {
      setSrtFile(srt);
      setPipelineMode("quick_burn");
    }
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) setSelectedFile(file);
  };

  const handleSrtSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSrtFile(file);
      setPipelineMode(srtMode === "translate" ? "skip_transcription" : "quick_burn");
    }
  };

  const toggleLanguage = (code: string) => {
    setSelectedLanguages(prev =>
      prev.includes(code)
        ? prev.filter(c => c !== code)
        : [...prev, code]
    );
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    setUploading(true);
    setError(null);
    setUploadProgress(0);

    try {
      const formData = new FormData();
      formData.append("file_0", selectedFile);
      if (srtFile) {
        formData.append("file_1", srtFile);
      }
      formData.append("mode", pipelineMode);
      formData.append("languages", JSON.stringify(selectedLanguages));
      formData.append("review_mode", reviewMode);

      // Use XMLHttpRequest for progress tracking
      const xhr = new XMLHttpRequest();

      await new Promise<void>((resolve, reject) => {
        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            setUploadProgress(Math.round((e.loaded / e.total) * 100));
          }
        });

        xhr.addEventListener("load", () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            try {
              const response = JSON.parse(xhr.responseText);
              reject(new Error(response.error || `Upload failed: ${xhr.status}`));
            } catch {
              reject(new Error(`Upload failed: ${xhr.status}`));
            }
          }
        });

        xhr.addEventListener("error", () => reject(new Error("Network error")));
        // Upload directly to FastAPI backend, bypassing the Next.js rewrite proxy
        // which buffers the entire body and causes socket hang-up for multi-GB files.
        xhr.open("POST", `${getBackendDirectUrl()}/api/v2/programs/upload`);
        const token = getStoredToken();
        if (token) {
          xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        }
        xhr.send(formData);
      });

      setSuccess(true);
      setTimeout(() => {
        onSuccess?.();
        onClose();
        // Reset state
        setSelectedFile(null);
        setSrtFile(null);
        setSrtMode("burn");
        setSelectedLanguages(["is"]);
        setPipelineMode("full_pipeline");
        setReviewMode("automatic");
        setSuccess(false);
        setUploadProgress(0);
      }, 1500);

    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="import-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Import Media</h2>
          <button className="close-btn" onClick={onClose} disabled={uploading}>
            <X size={20} />
          </button>
        </div>

        <div className="modal-body">
          {/* File Drop Zone */}
          <div
            className={`drop-zone ${selectedFile ? "has-file" : ""}`}
            onDrop={handleDrop}
            onDragOver={e => e.preventDefault()}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,.wmv,.flv,.mts,.m2ts,.ts,.mxf,video/*"
              onChange={handleFileSelect}
              style={{ display: "none" }}
            />

            {selectedFile ? (
              <div className="selected-file">
                <Film size={32} />
                <span className="file-name">{selectedFile.name}</span>
                <span className="file-size">
                  {selectedFile.size >= 1024 * 1024 * 1024
                    ? `${(selectedFile.size / (1024 * 1024 * 1024)).toFixed(2)} GB`
                    : `${(selectedFile.size / (1024 * 1024)).toFixed(1)} MB`}
                </span>
              </div>
            ) : (
              <>
                <Upload size={40} />
                <p>Drop video file here or click to browse</p>
                <span className="hint">MP4, MOV, MKV, AVI, WebM, MTS, ProRes and more</span>
              </>
            )}
          </div>

          {/* Optional SRT */}
          <div className="srt-section">
            <label>
              <input
                type="checkbox"
                checked={!!srtFile}
                onChange={() => {
                  if (srtFile) {
                    setSrtFile(null);
                    setSrtMode("burn");
                    setPipelineMode("full_pipeline");
                  } else {
                    srtInputRef.current?.click();
                  }
                }}
              />
              I have an existing SRT file
            </label>
            <input
              ref={srtInputRef}
              type="file"
              accept=".srt"
              onChange={handleSrtSelect}
              style={{ display: "none" }}
            />
            {srtFile && (
              <>
                <div className="srt-file">
                  <FileText size={16} />
                  <span>{srtFile.name}</span>
                </div>
                <div className="srt-mode-options">
                  <label className={`srt-mode-option ${srtMode === "translate" ? "selected" : ""}`}>
                    <input
                      type="radio"
                      name="srtMode"
                      value="translate"
                      checked={srtMode === "translate"}
                      onChange={() => {
                        setSrtMode("translate");
                        setPipelineMode("skip_transcription");
                      }}
                      style={{ position: "absolute", opacity: 0 }}
                    />
                    <Languages size={18} />
                    <div className="srt-mode-content">
                      <span className="srt-mode-title">Translate SRT</span>
                      <span className="srt-mode-desc">English SRT — translate to target language, then burn</span>
                    </div>
                  </label>
                  <label className={`srt-mode-option ${srtMode === "burn" ? "selected" : ""}`}>
                    <input
                      type="radio"
                      name="srtMode"
                      value="burn"
                      checked={srtMode === "burn"}
                      onChange={() => {
                        setSrtMode("burn");
                        setPipelineMode("quick_burn");
                      }}
                      style={{ position: "absolute", opacity: 0 }}
                    />
                    <Flame size={18} />
                    <div className="srt-mode-content">
                      <span className="srt-mode-title">Burn as-is</span>
                      <span className="srt-mode-desc">Already translated — burn directly onto video</span>
                    </div>
                  </label>
                </div>
              </>
            )}
          </div>

          {/* Pipeline Strategy */}
          {(!srtFile || srtMode === "translate") && (
            <div className="section" style={{ marginTop: "16px" }}>
              <h3>Pipeline Strategy</h3>
              <div className="mode-options" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", flexDirection: "row" }}>
                <label className={`mode-option ${reviewMode === "automatic" ? "selected" : ""}`} style={{ flexDirection: "column", alignItems: "center", textAlign: "center", padding: "24px 16px" }}>
                  <input
                    type="radio"
                    name="reviewMode"
                    value="automatic"
                    checked={reviewMode === "automatic"}
                    onChange={() => setReviewMode("automatic")}
                    style={{ position: "absolute", opacity: 0 }}
                  />
                  <div style={{ marginBottom: "16px", color: reviewMode === "automatic" ? "rgb(var(--omega-blue))" : "rgb(var(--omega-text-3))", transition: "color 0.2s" }}>
                    <Zap size={32} />
                  </div>
                  <div className="mode-content" style={{ alignItems: "center", width: "100%" }}>
                    <span className="mode-title" style={{ fontSize: "16px", marginBottom: "6px" }}>Zero-Touch Automation</span>
                    <span className="mode-desc" style={{ lineHeight: "1.4" }}>
                      Fully automated from upload to final burn. No human intervention needed.
                    </span>
                  </div>
                </label>
                <label className={`mode-option ${reviewMode === "human" ? "selected" : ""}`} style={{ flexDirection: "column", alignItems: "center", textAlign: "center", padding: "24px 16px" }}>
                  <input
                    type="radio"
                    name="reviewMode"
                    value="human"
                    checked={reviewMode === "human"}
                    onChange={() => setReviewMode("human")}
                    style={{ position: "absolute", opacity: 0 }}
                  />
                  <div style={{ marginBottom: "16px", color: reviewMode === "human" ? "rgb(var(--omega-blue))" : "rgb(var(--omega-text-3))", transition: "color 0.2s" }}>
                    <ShieldAlert size={32} />
                  </div>
                  <div className="mode-content" style={{ alignItems: "center", width: "100%" }}>
                    <span className="mode-title" style={{ fontSize: "16px", marginBottom: "6px" }}>Human Review</span>
                    <span className="mode-desc" style={{ lineHeight: "1.4" }}>
                      Pause pipeline after translation for manual editor polish before final burning.
                    </span>
                  </div>
                </label>
              </div>
            </div>
          )}

          {/* Language Selection */}
          {(!srtFile || srtMode === "translate") && (
            <div className="section" style={{ marginTop: "32px" }}>
              <h3>Target Languages</h3>
              <div className="language-grid">
                {AVAILABLE_LANGUAGES.map(lang => (
                  <button
                    key={lang.code}
                    type="button"
                    className={`language-btn ${selectedLanguages.includes(lang.code) ? "selected" : ""}`}
                    onClick={() => toggleLanguage(lang.code)}
                  >
                    <span className="flag">{lang.flag}</span>
                    <span className="name">{lang.name}</span>
                    {selectedLanguages.includes(lang.code) && <Check size={16} />}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          {/* Success Message */}
          {success && (
            <div className="success-message">
              <Check size={20} />
              Import started successfully!
            </div>
          )}

          {/* Progress */}
          {uploading && (
            <div className="upload-progress">
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${uploadProgress}%` }} />
              </div>
              <span>{uploadProgress}% uploaded</span>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button
            className="btn-cancel"
            onClick={onClose}
            disabled={uploading}
          >
            Cancel
          </button>
          <button
            className="btn-import"
            onClick={handleUpload}
            disabled={!selectedFile || uploading || success}
          >
            {uploading ? (
              <>
                <Loader2 size={16} className="spin" />
                Uploading...
              </>
            ) : success ? (
              <>
                <Check size={16} />
                Done!
              </>
            ) : (
              <>
                <Upload size={16} />
                Start Import
              </>
            )}
          </button>
        </div>

        <style jsx>{`
          .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.8);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
          }

          .import-modal {
            background: rgb(var(--omega-surface));
            border-radius: 12px;
            width: 90%;
            max-width: 560px;
            max-height: 90vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
          }

          .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 24px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
          }

          .modal-header h2 {
            font-size: 18px;
            font-weight: 600;
            color: rgb(var(--omega-text-1));
            margin: 0;
          }

          .close-btn {
            background: none;
            border: none;
            color: rgb(var(--omega-text-3));
            cursor: pointer;
            padding: 4px;
          }

          .close-btn:hover {
            color: rgb(var(--omega-text-1));
          }

          .modal-body {
            padding: 24px;
            overflow-y: auto;
            flex: 1;
          }

          .drop-zone {
            border: 2px dashed rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
            color: rgb(var(--omega-text-2));
          }

          .drop-zone:hover {
            border-color: rgb(var(--omega-blue));
            background: rgba(59, 130, 246, 0.05);
          }

          .drop-zone.has-file {
            border-color: rgb(var(--omega-green));
            background: rgba(34, 197, 94, 0.05);
          }

          .drop-zone p {
            margin: 12px 0 4px;
            font-size: 14px;
          }

          .drop-zone .hint {
            font-size: 12px;
            color: rgb(var(--omega-text-3));
          }

          .selected-file {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            color: rgb(var(--omega-green));
          }

          .file-name {
            font-weight: 500;
            color: rgb(var(--omega-text-1));
          }

          .file-size {
            font-size: 12px;
            color: rgb(var(--omega-text-3));
          }

          .srt-section {
            margin-top: 16px;
            padding: 12px;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 6px;
          }

          .srt-section label {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: rgb(var(--omega-text-2));
            cursor: pointer;
          }

          .srt-file {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-top: 8px;
            padding: 8px 12px;
            background: rgba(59, 130, 246, 0.1);
            border-radius: 4px;
            font-size: 13px;
            color: rgb(var(--omega-blue));
          }

          .srt-mode-options {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin-top: 10px;
          }

          .srt-mode-option {
            position: relative;
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 12px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            color: rgb(var(--omega-text-3));
          }

          .srt-mode-option:hover {
            background: rgba(255, 255, 255, 0.05);
          }

          .srt-mode-option.selected {
            background: rgba(59, 130, 246, 0.1);
            border-color: rgb(var(--omega-blue));
            color: rgb(var(--omega-blue));
          }

          .srt-mode-content {
            display: flex;
            flex-direction: column;
            gap: 2px;
          }

          .srt-mode-title {
            font-size: 13px;
            font-weight: 500;
            color: rgb(var(--omega-text-1));
          }

          .srt-mode-desc {
            font-size: 11px;
            color: rgb(var(--omega-text-3));
            line-height: 1.3;
          }

          .srt-mode-option.selected .srt-mode-title {
            color: rgb(var(--omega-blue));
          }

          .section {
            margin-top: 24px;
          }

          .section h3 {
            font-size: 13px;
            font-weight: 600;
            color: rgb(var(--omega-text-2));
            margin: 0 0 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
          }

          .language-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
          }

          .language-btn {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            color: rgb(var(--omega-text-2));
          }

          .language-btn:hover {
            background: rgba(255, 255, 255, 0.08);
          }

          .language-btn.selected {
            background: rgba(59, 130, 246, 0.15);
            border-color: rgb(var(--omega-blue));
            color: rgb(var(--omega-blue));
          }

          .language-btn .flag {
            font-size: 16px;
          }

          .language-btn .name {
            flex: 1;
            font-size: 13px;
          }

          .mode-options {
            display: flex;
            flex-direction: column;
            gap: 8px;
          }

          .mode-option {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 14px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
          }

          .mode-option:hover {
            background: rgba(255, 255, 255, 0.05);
          }

          .mode-option.selected {
            background: rgba(59, 130, 246, 0.1);
            border-color: rgb(var(--omega-blue));
          }

          .mode-option input {
            margin-top: 2px;
          }

          .mode-content {
            display: flex;
            flex-direction: column;
            gap: 4px;
          }

          .mode-title {
            font-weight: 500;
            color: rgb(var(--omega-text-1));
          }

          .mode-desc {
            font-size: 12px;
            color: rgb(var(--omega-text-3));
          }

          .error-message {
            margin-top: 16px;
            padding: 12px;
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.3);
            border-radius: 6px;
            color: rgb(239, 68, 68);
            font-size: 13px;
          }

          .success-message {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            margin-top: 16px;
            padding: 12px;
            background: rgba(34, 197, 94, 0.1);
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-radius: 6px;
            color: rgb(34, 197, 94);
            font-size: 14px;
            font-weight: 500;
          }

          .upload-progress {
            margin-top: 16px;
          }

          .progress-bar {
            height: 4px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
            overflow: hidden;
          }

          .progress-fill {
            height: 100%;
            background: rgb(var(--omega-blue));
            transition: width 0.3s;
          }

          .upload-progress span {
            display: block;
            margin-top: 6px;
            font-size: 12px;
            color: rgb(var(--omega-text-3));
            text-align: center;
          }

          .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            padding: 16px 24px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
          }

          .btn-cancel {
            padding: 10px 20px;
            background: none;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            color: rgb(var(--omega-text-2));
            font-size: 14px;
            cursor: pointer;
          }

          .btn-cancel:hover:not(:disabled) {
            background: rgba(255, 255, 255, 0.05);
          }

          .btn-import {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 24px;
            background: rgb(var(--omega-blue));
            border: none;
            border-radius: 6px;
            color: white;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
          }

          .btn-import:hover:not(:disabled) {
            filter: brightness(1.1);
          }

          .btn-import:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .spin {
            animation: spin 1s linear infinite;
          }

          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}</style>
      </div>
    </div>
  );
}
