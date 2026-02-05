"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Modal from "@/components/common/Modal";
import Button from "@/components/common/Button";
import {
  LanguageOption,
  VoiceOption,
  useProgramsStore,
} from "@/store/programs";

interface AddTrackModalProps {
  open: boolean;
  programId: string;
  onClose: () => void;
}

type TrackType = "subtitle" | "dub";

const normalizeMode = (mode?: string): TrackType => {
  if (!mode) return "subtitle";
  return mode.toLowerCase() === "dub" ? "dub" : "subtitle";
};

const getDefaultVoice = (language: LanguageOption | undefined, voices: VoiceOption[]): string => {
  if (language?.default_voice) return language.default_voice;
  return voices[0]?.id || "";
};

export default function AddTrackModal({ open, programId, onClose }: AddTrackModalProps) {
  const {
    languages,
    voices,
    fetchLanguages,
    fetchVoices,
    addTrack,
  } = useProgramsStore();
  const [languageCode, setLanguageCode] = useState("");
  const [trackType, setTrackType] = useState<TrackType>("subtitle");
  const [voiceId, setVoiceId] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const sortedLanguages = useMemo(
    () => [...languages].sort((a, b) => a.name.localeCompare(b.name)),
    [languages]
  );

  const selectedLanguage = useMemo(
    () => sortedLanguages.find((lang) => lang.code === languageCode),
    [sortedLanguages, languageCode]
  );

  const selectedVoice = useMemo(
    () => voices.find((voice) => voice.id === voiceId),
    [voices, voiceId]
  );

  useEffect(() => {
    if (!open) return;
    if (!languages.length) {
      fetchLanguages();
    }
    if (!voices.length) {
      fetchVoices();
    }
  }, [open, languages.length, voices.length, fetchLanguages, fetchVoices]);

  useEffect(() => {
    if (!open) return;
    if (!languageCode && sortedLanguages.length) {
      setLanguageCode(sortedLanguages[0].code);
    }
  }, [open, languageCode, sortedLanguages]);

  useEffect(() => {
    if (!open || !selectedLanguage) return;
    const nextType = normalizeMode(selectedLanguage.default_mode);
    setTrackType(nextType);
    if (nextType === "dub") {
      setVoiceId(getDefaultVoice(selectedLanguage, voices));
    } else {
      setVoiceId("");
    }
    setError("");
  }, [open, selectedLanguage?.code, voices]);

  useEffect(() => {
    if (!open) {
      setLanguageCode("");
      setTrackType("subtitle");
      setVoiceId("");
      setError("");
      setIsSubmitting(false);
      return;
    }
    if (trackType === "dub" && !voiceId) {
      const defaultVoice = getDefaultVoice(selectedLanguage, voices);
      if (defaultVoice) {
        setVoiceId(defaultVoice);
      }
    }
  }, [open, trackType, voiceId, selectedLanguage, voices]);

  const handleTypeChange = (nextType: TrackType) => {
    setTrackType(nextType);
    if (nextType === "dub") {
      const defaultVoice = getDefaultVoice(selectedLanguage, voices);
      setVoiceId((current) => current || defaultVoice);
    } else {
      setVoiceId("");
    }
    setError("");
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!languageCode) {
      setError("Select a language.");
      return;
    }
    if (trackType === "dub" && !voiceId) {
      setError("Voice required for dub tracks.");
      return;
    }

    setIsSubmitting(true);
    const result = await addTrack(
      programId,
      trackType,
      languageCode,
      trackType === "dub" ? voiceId : undefined
    );
    setIsSubmitting(false);

    if (!result.ok) {
      setError(result.error || "Failed to add track.");
      return;
    }

    onClose();
  };

  if (!open) return null;

  return (
    <Modal onClose={onClose} overlayClassName="add-track-overlay" panelClassName="add-track-panel">
      <div className="add-track-modal">
        <div className="add-track-header">
          <div>
            <div className="detail-title">Add Track</div>
            <div className="page-subtitle">Create a new subtitle or dubbing track.</div>
          </div>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>

        <form className="add-track-form" onSubmit={handleSubmit}>
          <div className="form-row">
            <label className="form-label" htmlFor="language">
              Language
            </label>
            <select
              id="language"
              className="input"
              value={languageCode}
              onChange={(event) => setLanguageCode(event.target.value)}
            >
              {!sortedLanguages.length && <option value="">Loading languages...</option>}
              {sortedLanguages.map((lang) => (
                <option key={lang.code} value={lang.code}>
                  {lang.name}
                </option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <span className="form-label">Type</span>
            <div className="type-toggle">
              <button
                type="button"
                className={`toggle-option ${trackType === "subtitle" ? "toggle-option--active" : ""}`}
                onClick={() => handleTypeChange("subtitle")}
                aria-pressed={trackType === "subtitle"}
              >
                Subtitle
              </button>
              <button
                type="button"
                className={`toggle-option ${trackType === "dub" ? "toggle-option--active" : ""}`}
                onClick={() => handleTypeChange("dub")}
                aria-pressed={trackType === "dub"}
              >
                Dubbing
              </button>
            </div>
            <div className="form-helper">
              Default mode for this language is {normalizeMode(selectedLanguage?.default_mode)}.
            </div>
          </div>

          {trackType === "dub" && (
            <div className="form-row">
              <label className="form-label" htmlFor="voice">
                Voice
              </label>
              <select
                id="voice"
                className="input"
                value={voiceId}
                onChange={(event) => setVoiceId(event.target.value)}
              >
                {!voices.length && <option value="">Loading voices...</option>}
                {voices.map((voice) => (
                  <option key={voice.id} value={voice.id}>
                    {voice.name}
                  </option>
                ))}
              </select>
              {selectedVoice?.description && (
                <div className="form-helper">{selectedVoice.description}</div>
              )}
            </div>
          )}

          {error && <div className="form-error">{error}</div>}

          <div className="add-track-footer">
            <Button variant="ghost" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              disabled={
                isSubmitting ||
                !languageCode ||
                (trackType === "dub" && !voiceId)
              }
            >
              {isSubmitting ? "Creating..." : "Create Track"}
            </Button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
