"use client";

import { useState, useRef } from "react";
import { UploadCloud, Loader2 } from "lucide-react";

interface UploadZoneProps {
    onUploadComplete: () => void;
}

export function UploadZone({ onUploadComplete }: UploadZoneProps) {
    const [uploading, setUploading] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            await uploadFiles(e.target.files);
        }
    };

    const uploadFiles = async (files: FileList) => {
        setUploading(true);
        try {
            for (let i = 0; i < files.length; i++) {
                const formData = new FormData();
                formData.append("file", files[i]);
                const res = await fetch("/api/upload", { method: "POST", body: formData });
                if (!res.ok) throw new Error("Upload Failed");
            }
            onUploadComplete();
        } catch (err) {
            console.error(err);
            alert("Import Failed");
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    return (
        <div>
            <input
                type="file"
                ref={fileInputRef}
                onChange={handleSelect}
                className="hidden"
                multiple
                accept="video/*,audio/*,.mp4,.mov,.mp3,.wav"
            />
            <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="btn btn-primary"
            >
                {uploading ? (
                    <Loader2 className="w-4 h-4 spin" />
                ) : (
                    <UploadCloud className="w-4 h-4" />
                )}
                Import
            </button>
        </div>
    );
}
