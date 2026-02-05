import { useState } from "react";
import { Globe, Copy, Loader2, Check } from "lucide-react";


// These should ideally match backend profiles.py
const LANGUAGES = [
    { code: "is", name: "Icelandic", flag: "🇮🇸", defaultMode: "sub" },
    { code: "de", name: "German", flag: "🇩🇪", defaultMode: "dub" },
    { code: "es", name: "Spanish", flag: "🇪🇸", defaultMode: "dub" },
    { code: "fr", name: "French", flag: "🇫🇷", defaultMode: "dub" },
    { code: "it", name: "Italian", flag: "🇮🇹", defaultMode: "dub" },
    { code: "pt", name: "Portuguese", flag: "🇵🇹", defaultMode: "sub" },
    { code: "no", name: "Norwegian", flag: "🇳🇴", defaultMode: "sub" },
    { code: "sv", name: "Swedish", flag: "🇸🇪", defaultMode: "sub" },
    { code: "da", name: "Danish", flag: "🇩🇰", defaultMode: "sub" },
];

interface LanguageForkModalProps {
    jobId: string;
    isOpen: boolean;
    onClose: () => void;
    onSuccess: () => void;
}

export function LanguageForkModal({ jobId, isOpen, onClose, onSuccess }: LanguageForkModalProps) {
    const [selectedLangs, setSelectedLangs] = useState<string[]>(["is"]); // Default to Icelandic
    const [isForking, setIsForking] = useState(false);

    if (!isOpen) return null;

    const toggleLang = (code: string) => {
        setSelectedLangs(prev =>
            prev.includes(code) ? prev.filter(c => c !== code) : [...prev, code]
        );
    };

    const handleFork = async () => {
        if (selectedLangs.length === 0) return;
        setIsForking(true);
        try {
            const res = await fetch("/api/action/fork", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jobId,
                    languages: selectedLangs
                })
            });
            if (!res.ok) throw new Error("Fork failed");
            onSuccess();
            onClose();
        } catch (e) {
            alert("Fork failed: " + e);
        } finally {
            setIsForking(false);
        }
    };

    return (
        <div style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50
        }}>
            <div style={{
                width: 400, background: "#18181b", border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 12, padding: 24, boxShadow: "0 20px 50px rgba(0,0,0,0.5)"
            }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                    <div style={{ padding: 8, background: "rgba(82,139,255,0.1)", borderRadius: 8 }}>
                        <Globe className="text-[#528BFF]" size={20} />
                    </div>
                    <div>
                        <h2 style={{ fontSize: 16, fontWeight: 600, color: "#f5f5f5", margin: 0 }}>Localize Program</h2>
                        <p style={{ fontSize: 12, color: "#9ca3af", margin: 0 }}>Select target languages for distribution.</p>
                    </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, maxHeight: 300, overflowY: "auto", marginBottom: 24 }}>
                    {LANGUAGES.map(lang => {
                        const isSelected = selectedLangs.includes(lang.code);
                        return (
                            <button
                                key={lang.code}
                                onClick={() => toggleLang(lang.code)}
                                style={{
                                    display: "flex", alignItems: "center", gap: 8,
                                    padding: "10px 12px", borderRadius: 8,
                                    background: isSelected ? "rgba(82,139,255,0.1)" : "rgba(255,255,255,0.03)",
                                    border: isSelected ? "1px solid #528BFF" : "1px solid rgba(255,255,255,0.06)",
                                    cursor: "pointer", textAlign: "left"
                                }}
                            >
                                <span style={{ fontSize: 16 }}>{lang.flag}</span>
                                <div style={{ flex: 1 }}>
                                    <div style={{ fontSize: 13, fontWeight: 500, color: isSelected ? "#f5f5f5" : "#d4d4d4" }}>{lang.name}</div>
                                    <div style={{ fontSize: 11, color: isSelected ? "#93c5fd" : "#6b7280" }}>
                                        {lang.defaultMode === "dub" ? "🎙️ AI Dubbing" : "📝 Subtitles"}
                                    </div>
                                </div>
                                {isSelected && <Check size={14} className="text-[#528BFF]" />}
                            </button>
                        );
                    })}
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
                    <button
                        onClick={onClose}
                        style={{ padding: "8px 16px", borderRadius: 6, background: "transparent", border: "1px solid rgba(255,255,255,0.1)", color: "#d4d4d4", fontSize: 13, cursor: "pointer" }}
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleFork}
                        disabled={isForking || selectedLangs.length === 0}
                        style={{
                            display: "flex", alignItems: "center", gap: 8,
                            padding: "8px 16px", borderRadius: 6,
                            background: "#528BFF", border: "none",
                            color: "#fff", fontSize: 13, fontWeight: 500,
                            cursor: (isForking || selectedLangs.length === 0) ? "not-allowed" : "pointer",
                            opacity: (isForking || selectedLangs.length === 0) ? 0.7 : 1
                        }}
                    >
                        {isForking ? <Loader2 size={14} className="animate-spin" /> : <Copy size={14} />}
                        {isForking ? "Creating Jobs..." : `Localize (${selectedLangs.length})`}
                    </button>
                </div>
            </div>
        </div>
    );
}
