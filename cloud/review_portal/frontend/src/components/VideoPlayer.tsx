import { useEffect, useRef } from "react";

interface VideoPlayerProps {
  videoUrl: string;
  currentSubtitle: string;
  timestamp: number;
}

export function VideoPlayer({ videoUrl, currentSubtitle, timestamp }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!videoRef.current || !timestamp) return;
    videoRef.current.currentTime = Math.max(0, timestamp - 2);
    const playPromise = videoRef.current.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch(() => undefined);
    }
  }, [timestamp]);

  const subtitle = currentSubtitle?.trim();

  if (!videoUrl) {
    return (
      <div className="relative w-full aspect-video rounded-2xl border border-review-border bg-black/60 overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(74,222,128,0.12),_transparent_60%)]" />
        <div className="relative z-10 h-full w-full flex items-center justify-center">
          <div className="text-center space-y-2">
            <p className="text-xl font-semibold">Video ready for review</p>
            <p className="text-sm text-gray-400">Connect a preview URL to start playback</p>
          </div>
        </div>
      </div>
    );
  }

  if (videoUrl.includes("iframe.mediadelivery.net") || videoUrl.includes("embed")) {
    return (
      <div className="relative w-full aspect-video bg-black rounded-2xl overflow-hidden border border-review-border">
        <iframe
          src={videoUrl}
          className="w-full h-full"
          allowFullScreen
          allow="autoplay; encrypted-media"
          title="Review video"
        />
        {subtitle ? (
          <div className="absolute bottom-6 left-0 right-0 flex justify-center px-6">
            <span className="bg-black/80 text-white text-lg px-4 py-2 rounded-full backdrop-blur">
              {subtitle}
            </span>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="relative w-full aspect-video bg-black rounded-2xl overflow-hidden border border-review-border">
      <video ref={videoRef} src={videoUrl} className="w-full h-full" controls />
      {subtitle ? (
        <div className="absolute bottom-6 left-0 right-0 flex justify-center px-6">
          <span className="bg-black/80 text-white text-lg px-4 py-2 rounded-full backdrop-blur">
            {subtitle}
          </span>
        </div>
      ) : null}
    </div>
  );
}
