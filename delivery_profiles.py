"""
Omega Delivery Profiles
=======================

Professional video delivery presets for the burn agent.

Supports TV broadcast, streaming platforms, and custom outputs.

Usage:
    from delivery_profiles import get_delivery_profile, DELIVERY_PROFILES
    
    profile = get_delivery_profile("netflix_1080p")
    ffmpeg_args = profile["ffmpeg_args"]

Environment Variable:
    OMEGA_BURN_DELIVERY_PROFILE - Default profile to use (e.g., "source", "1080p", "netflix_1080p")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# =============================================================================
# Delivery Profile Definition
# =============================================================================

@dataclass
class DeliveryProfile:
    """
    A delivery profile defines encoding settings for a specific output format.
    """
    name: str
    description: str
    
    # Video settings
    resolution: Optional[str] = None  # e.g., "1920x1080", None = keep source
    framerate: Optional[str] = None   # e.g., "29.97", "25", None = keep source
    video_codec: str = "libx264"      # libx264, libx265, hevc_videotoolbox, h264_videotoolbox
    video_bitrate: Optional[str] = None  # e.g., "8M", None = auto/CRF
    crf: Optional[int] = None         # Constant Rate Factor (18-28 typical)
    preset: str = "medium"            # ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
    profile: Optional[str] = None     # baseline, main, high
    level: Optional[str] = None       # 3.1, 4.0, 4.1, 5.0
    pix_fmt: str = "yuv420p"          # yuv420p, yuv422p, yuv444p
    
    # Audio settings
    audio_codec: str = "aac"          # aac, ac3, libmp3lame, copy
    audio_bitrate: str = "192k"       # e.g., "128k", "192k", "256k"
    audio_channels: Optional[int] = None  # 2 = stereo, 6 = 5.1, None = keep
    audio_sample_rate: Optional[int] = None  # 44100, 48000, None = keep
    
    # Container
    container: str = "mp4"            # mp4, mov, mkv
    
    # Hardware acceleration
    hwaccel: Optional[str] = None     # None, "videotoolbox", "cuda", "qsv"
    
    # Additional FFmpeg arguments
    extra_args: List[str] = field(default_factory=list)
    
    def build_ffmpeg_args(self, input_path: str, srt_path: str, output_path: str) -> List[str]:
        """
        Build complete FFmpeg command arguments.
        """
        args = ["ffmpeg", "-y"]
        
        # Hardware acceleration (input side)
        if self.hwaccel:
            args.extend(["-hwaccel", self.hwaccel])
        
        # Input
        args.extend(["-i", input_path])
        
        # Build filter chain
        filters = []
        
        # Subtitles filter
        # Escape special characters in SRT path
        escaped_srt = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        filters.append(f"subtitles='{escaped_srt}'")
        
        # Resolution scaling (if specified)
        if self.resolution:
            w, h = self.resolution.split("x")
            filters.append(f"scale={w}:{h}:flags=lanczos")
        
        # Framerate filter (if specified)
        if self.framerate:
            filters.append(f"fps={self.framerate}")
        
        # Apply video filters
        if filters:
            args.extend(["-vf", ",".join(filters)])
        
        # Video codec
        args.extend(["-c:v", self.video_codec])
        
        # Video quality settings
        if self.crf is not None:
            args.extend(["-crf", str(self.crf)])
        elif self.video_bitrate:
            args.extend(["-b:v", self.video_bitrate])
        
        # Encoding preset
        if self.video_codec in ("libx264", "libx265"):
            args.extend(["-preset", self.preset])
        
        # Profile and level
        if self.profile:
            args.extend(["-profile:v", self.profile])
        if self.level:
            args.extend(["-level", self.level])
        
        # Pixel format
        args.extend(["-pix_fmt", self.pix_fmt])
        
        # Audio settings
        args.extend(["-c:a", self.audio_codec])
        
        if self.audio_codec != "copy":
            args.extend(["-b:a", self.audio_bitrate])
            if self.audio_channels:
                args.extend(["-ac", str(self.audio_channels)])
            if self.audio_sample_rate:
                args.extend(["-ar", str(self.audio_sample_rate)])
        
        # Container-specific options
        if self.container == "mp4":
            args.extend(["-movflags", "+faststart"])
        
        # Extra arguments
        args.extend(self.extra_args)
        
        # Output
        args.append(output_path)
        
        return args
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "resolution": self.resolution,
            "framerate": self.framerate,
            "video_codec": self.video_codec,
            "video_bitrate": self.video_bitrate,
            "crf": self.crf,
            "preset": self.preset,
            "audio_codec": self.audio_codec,
            "container": self.container,
        }


# =============================================================================
# Preset Delivery Profiles
# =============================================================================

DELIVERY_PROFILES: Dict[str, DeliveryProfile] = {
    # -------------------------------------------------------------------------
    # Source-Preserving (Default)
    # -------------------------------------------------------------------------
    "source": DeliveryProfile(
        name="Source Quality",
        description="Keep source resolution and framerate, high quality encoding",
        crf=18,
        preset="medium",
        profile="high",
    ),
    
    "source_fast": DeliveryProfile(
        name="Source Quality (Fast)",
        description="Keep source resolution, faster encoding for quick previews",
        crf=23,
        preset="fast",
        profile="main",
    ),
    
    # -------------------------------------------------------------------------
    # Standard Resolutions
    # -------------------------------------------------------------------------
    "1080p": DeliveryProfile(
        name="1080p HD",
        description="Full HD 1920x1080",
        resolution="1920x1080",
        crf=20,
        preset="medium",
        profile="high",
    ),
    
    "720p": DeliveryProfile(
        name="720p HD",
        description="HD 1280x720, smaller file size",
        resolution="1280x720",
        crf=22,
        preset="medium",
        profile="main",
    ),
    
    "480p": DeliveryProfile(
        name="480p SD",
        description="Standard definition, for low bandwidth",
        resolution="854x480",
        crf=23,
        preset="medium",
        profile="main",
    ),
    
    "4k": DeliveryProfile(
        name="4K UHD",
        description="4K Ultra HD 3840x2160",
        resolution="3840x2160",
        video_codec="libx265",  # HEVC for 4K
        crf=22,
        preset="slow",  # Better quality for 4K
        profile="main",
        pix_fmt="yuv420p10le",  # 10-bit for HDR support
    ),
    
    # -------------------------------------------------------------------------
    # Netflix Delivery Specs
    # https://partnerhelp.netflixstudios.com/hc/en-us/articles/215148917
    # -------------------------------------------------------------------------
    "netflix_1080p": DeliveryProfile(
        name="Netflix 1080p",
        description="Netflix HD delivery spec",
        resolution="1920x1080",
        video_codec="libx264",
        video_bitrate="10M",
        preset="slow",
        profile="high",
        level="4.0",
        audio_codec="aac",
        audio_bitrate="256k",
        audio_channels=2,
        audio_sample_rate=48000,
        extra_args=["-maxrate", "10M", "-bufsize", "20M"],
    ),
    
    "netflix_4k": DeliveryProfile(
        name="Netflix 4K",
        description="Netflix UHD delivery spec",
        resolution="3840x2160",
        video_codec="libx265",
        video_bitrate="16M",
        preset="slow",
        profile="main10",
        level="5.1",
        pix_fmt="yuv420p10le",
        audio_codec="aac",
        audio_bitrate="384k",
        audio_channels=6,
        audio_sample_rate=48000,
    ),
    
    # -------------------------------------------------------------------------
    # YouTube Recommended
    # https://support.google.com/youtube/answer/1722171
    # -------------------------------------------------------------------------
    "youtube_1080p": DeliveryProfile(
        name="YouTube 1080p",
        description="YouTube recommended settings for 1080p",
        resolution="1920x1080",
        framerate="30",
        video_codec="libx264",
        video_bitrate="8M",
        preset="slow",
        profile="high",
        audio_codec="aac",
        audio_bitrate="384k",
        audio_channels=2,
        audio_sample_rate=48000,
    ),
    
    "youtube_1080p60": DeliveryProfile(
        name="YouTube 1080p60",
        description="YouTube 1080p at 60fps",
        resolution="1920x1080",
        framerate="60",
        video_codec="libx264",
        video_bitrate="12M",
        preset="slow",
        profile="high",
        level="4.2",
        audio_codec="aac",
        audio_bitrate="384k",
    ),
    
    "youtube_4k": DeliveryProfile(
        name="YouTube 4K",
        description="YouTube 4K recommended settings",
        resolution="3840x2160",
        framerate="30",
        video_codec="libx265",
        video_bitrate="40M",
        preset="slow",
        profile="main",
        audio_codec="aac",
        audio_bitrate="512k",
    ),
    
    # -------------------------------------------------------------------------
    # TV Broadcast Formats
    # -------------------------------------------------------------------------
    "broadcast_pal": DeliveryProfile(
        name="PAL Broadcast",
        description="European TV broadcast (25fps, 1080i/p)",
        resolution="1920x1080",
        framerate="25",
        video_codec="libx264",
        video_bitrate="15M",
        preset="slow",
        profile="high",
        level="4.0",
        audio_codec="ac3",  # Dolby Digital for broadcast
        audio_bitrate="384k",
        audio_channels=2,
        audio_sample_rate=48000,
        extra_args=["-g", "12", "-bf", "2"],  # GOP and B-frames for broadcast
    ),
    
    "broadcast_ntsc": DeliveryProfile(
        name="NTSC Broadcast",
        description="North American TV broadcast (29.97fps, 1080i/p)",
        resolution="1920x1080",
        framerate="29.97",
        video_codec="libx264",
        video_bitrate="15M",
        preset="slow",
        profile="high",
        level="4.0",
        audio_codec="ac3",
        audio_bitrate="384k",
        audio_channels=2,
        audio_sample_rate=48000,
        extra_args=["-g", "15", "-bf", "2"],
    ),
    
    "broadcast_iceland": DeliveryProfile(
        name="Iceland Broadcast (RÚV)",
        description="Icelandic national broadcaster specs",
        resolution="1920x1080",
        framerate="25",  # PAL region
        video_codec="libx264",
        video_bitrate="12M",
        preset="slow",
        profile="high",
        audio_codec="aac",
        audio_bitrate="256k",
        audio_channels=2,
        audio_sample_rate=48000,
    ),
    
    # -------------------------------------------------------------------------
    # Hardware Accelerated (macOS)
    # -------------------------------------------------------------------------
    "mac_fast": DeliveryProfile(
        name="Mac Hardware Encode",
        description="Fast encoding using Apple VideoToolbox",
        video_codec="h264_videotoolbox",
        video_bitrate="10M",
        hwaccel="videotoolbox",
        audio_codec="aac",
        audio_bitrate="192k",
    ),
    
    "mac_hevc": DeliveryProfile(
        name="Mac HEVC Hardware",
        description="HEVC encoding using Apple VideoToolbox",
        video_codec="hevc_videotoolbox",
        video_bitrate="8M",
        hwaccel="videotoolbox",
        audio_codec="aac",
        audio_bitrate="192k",
    ),
    
    # -------------------------------------------------------------------------
    # Social Media
    # -------------------------------------------------------------------------
    "instagram": DeliveryProfile(
        name="Instagram/Reels",
        description="Optimized for Instagram (1:1 or 9:16)",
        resolution="1080x1080",  # Square - adjust for reels
        framerate="30",
        video_codec="libx264",
        video_bitrate="3.5M",
        preset="medium",
        profile="high",
        audio_codec="aac",
        audio_bitrate="128k",
    ),
    
    "twitter": DeliveryProfile(
        name="Twitter/X",
        description="Optimized for Twitter video",
        resolution="1280x720",
        framerate="30",
        video_codec="libx264",
        video_bitrate="5M",  # Twitter max
        preset="medium",
        profile="main",
        audio_codec="aac",
        audio_bitrate="128k",
    ),
}


# =============================================================================
# Profile Access Functions
# =============================================================================

def get_delivery_profile(name: str) -> DeliveryProfile:
    """
    Get a delivery profile by name.
    
    Falls back to 'source' if not found.
    """
    return DELIVERY_PROFILES.get(name.lower(), DELIVERY_PROFILES["source"])


def get_default_profile() -> DeliveryProfile:
    """Get the default delivery profile from environment or 'source'."""
    default = os.environ.get("OMEGA_BURN_DELIVERY_PROFILE", "source")
    return get_delivery_profile(default)


def list_profiles() -> List[Dict[str, Any]]:
    """List all available delivery profiles."""
    return [
        {"key": key, **profile.to_dict()}
        for key, profile in DELIVERY_PROFILES.items()
    ]


def create_custom_profile(
    name: str = "Custom",
    description: str = "Custom delivery profile",
    **kwargs
) -> DeliveryProfile:
    """
    Create a custom delivery profile with specified settings.
    
    Useful for one-off jobs with specific requirements.
    """
    return DeliveryProfile(name=name, description=description, **kwargs)


# =============================================================================
# Convenience Functions
# =============================================================================

def get_ffmpeg_args(
    input_path: str,
    srt_path: str,
    output_path: str,
    profile_name: str = "source",
) -> List[str]:
    """
    Get FFmpeg arguments for a specific delivery profile.
    
    Convenience function for simple use cases.
    """
    profile = get_delivery_profile(profile_name)
    return profile.build_ffmpeg_args(input_path, srt_path, output_path)


# =============================================================================
# Main (for testing)
# =============================================================================

if __name__ == "__main__":
    import json
    
    print("Available Delivery Profiles:")
    print("-" * 60)
    
    for key, profile in DELIVERY_PROFILES.items():
        print(f"\n{key}:")
        print(f"  Name: {profile.name}")
        print(f"  Description: {profile.description}")
        if profile.resolution:
            print(f"  Resolution: {profile.resolution}")
        if profile.framerate:
            print(f"  Framerate: {profile.framerate}")
        print(f"  Codec: {profile.video_codec}")
    
    # Example FFmpeg command
    print("\n" + "=" * 60)
    print("Example FFmpeg command for 'netflix_1080p':")
    args = get_ffmpeg_args(
        "/path/to/input.mp4",
        "/path/to/subtitle.srt",
        "/path/to/output.mp4",
        "netflix_1080p"
    )
    print(" ".join(args))
