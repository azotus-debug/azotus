from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class SystemState(Base):
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)

class Program(Base):
    __tablename__ = "programs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(Text)
    video_path: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    client: Mapped[Optional[str]] = mapped_column(String, index=True)
    due_date: Mapped[Optional[str]] = mapped_column(String)  # stored as string in sqlite/postgres
    default_style: Mapped[str] = mapped_column(String, default="Classic")
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    deleted_original_filename: Mapped[Optional[str]] = mapped_column(Text)
    deleted_video_path: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    tracks: Mapped[List["Track"]] = relationship("Track", back_populates="program", foreign_keys="[Track.program_id]")
    master_scripts: Mapped[List["MasterScript"]] = relationship("MasterScript", back_populates="program")

class MasterScript(Base):
    __tablename__ = "master_scripts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    program_id: Mapped[str] = mapped_column(String, ForeignKey("programs.id"), nullable=False)
    language_code: Mapped[str] = mapped_column(String, nullable=False)
    language_name: Mapped[Optional[str]] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    locked_by: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    program: Mapped["Program"] = relationship("Program", back_populates="master_scripts")
    tracks: Mapped[List["Track"]] = relationship("Track", back_populates="master_script", foreign_keys="[Track.master_script_id]")

class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    program_id: Mapped[str] = mapped_column(String, ForeignKey("programs.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String, default="subtitle", nullable=False)
    language_code: Mapped[str] = mapped_column(String, nullable=False)
    language_name: Mapped[Optional[str]] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String, default="QUEUED", index=True)
    state: Mapped[str] = mapped_column(String, default="draft")
    status: Mapped[str] = mapped_column(String, default="Pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    delivery_status: Mapped[str] = mapped_column(String, default="PENDING")
    job_id: Mapped[Optional[str]] = mapped_column(String)
    voice_id: Mapped[Optional[str]] = mapped_column(String)
    rating: Mapped[Optional[float]] = mapped_column(Float)
    output_path: Mapped[Optional[str]] = mapped_column(Text)
    depends_on: Mapped[Optional[str]] = mapped_column(String, ForeignKey("tracks.id"))
    master_script_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("master_scripts.id"))
    output_version: Mapped[str] = mapped_column(String, default="1.0")
    output_override: Mapped[int] = mapped_column(Integer, default=0)
    override_reason: Mapped[Optional[str]] = mapped_column(Text)
    override_author: Mapped[Optional[str]] = mapped_column(String)
    override_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime)
    pending_resync: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    locked_by: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    program: Mapped["Program"] = relationship("Program", back_populates="tracks", foreign_keys=[program_id])
    master_script: Mapped[Optional["MasterScript"]] = relationship("MasterScript", back_populates="tracks", foreign_keys=[master_script_id])

class ErrorLog(Base):
    __tablename__ = "error_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    error_type: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[Optional[str]] = mapped_column(Text)
    worker: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class Station(Base):
    __tablename__ = "stations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(String)
    tailscale_ip: Mapped[Optional[str]] = mapped_column(String)
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, default="offline", index=True)
    jobs_processed: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DeliveryProfile(Base):
    __tablename__ = "delivery_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    outputs: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)


class TrackDelivery(Base):
    __tablename__ = "track_deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    track_id: Mapped[str] = mapped_column(String, ForeignKey("tracks.id"), nullable=False)
    destination: Mapped[Optional[str]] = mapped_column(String)
    recipient: Mapped[Optional[str]] = mapped_column(String)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class ScriptEdit(Base):
    __tablename__ = "script_edits"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    master_script_id: Mapped[str] = mapped_column(String, ForeignKey("master_scripts.id"), nullable=False)
    track_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("tracks.id"))
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)


class StageTransition(Base):
    __tablename__ = "stage_transitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[str] = mapped_column(String, nullable=False)
    job_stem: Mapped[str] = mapped_column(String, nullable=False)
    from_stage: Mapped[Optional[str]] = mapped_column(String)
    to_stage: Mapped[str] = mapped_column(String, nullable=False)
    processing_step: Mapped[Optional[str]] = mapped_column(String)
    worker_id: Mapped[Optional[str]] = mapped_column(String)
    reason: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)


class BookProject(Base):
    __tablename__ = "book_projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String)
    source_language: Mapped[str] = mapped_column(String, default="en")
    target_language: Mapped[str] = mapped_column(String, default="is")
    source_file_path: Mapped[Optional[str]] = mapped_column(Text)
    total_chapters: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String, default="UPLOADING", index=True)
    status: Mapped[str] = mapped_column(String, default="Pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    glossary: Mapped[Optional[str]] = mapped_column(Text)
    character_bible: Mapped[Optional[str]] = mapped_column(Text)
    style_guide: Mapped[Optional[str]] = mapped_column(Text)
    translation_notes: Mapped[Optional[str]] = mapped_column(Text)
    client: Mapped[Optional[str]] = mapped_column(String, index=True)
    due_date: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    chapters: Mapped[List["BookChapter"]] = relationship("BookChapter", back_populates="book")


class BookChapter(Base):
    __tablename__ = "book_chapters"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    book_id: Mapped[str] = mapped_column(String, ForeignKey("book_projects.id"), nullable=False, index=True)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_title: Mapped[Optional[str]] = mapped_column(String)
    source_text: Mapped[Optional[str]] = mapped_column(Text)
    step1_translation: Mapped[Optional[str]] = mapped_column(Text)
    step1_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    step2_theology_review: Mapped[Optional[str]] = mapped_column(Text)
    step2_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    step3_polish: Mapped[Optional[str]] = mapped_column(Text)
    step3_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    final_text: Mapped[Optional[str]] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    status: Mapped[str] = mapped_column(String, default="Pending")
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    locked_by: Mapped[Optional[str]] = mapped_column(String)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    translation_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    book: Mapped["BookProject"] = relationship("BookProject", back_populates="chapters")


class MinistryProfile(Base):
    __tablename__ = "ministry_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    languages: Mapped[str] = mapped_column(Text, nullable=False)
    default_delivery_id: Mapped[Optional[str]] = mapped_column(String)
    terminology: Mapped[Optional[str]] = mapped_column(Text)
    style: Mapped[Optional[str]] = mapped_column(Text)
    watch_folder: Mapped[Optional[str]] = mapped_column(Text)
    workflow: Mapped[str] = mapped_column(String, default="standard")
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class DropzoneRecipe(Base):
    __tablename__ = "dropzone_recipes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    folder_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    ministry_id: Mapped[Optional[str]] = mapped_column(String)
    languages: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_id: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_stem: Mapped[Optional[str]] = mapped_column(Text)
    client: Mapped[Optional[str]] = mapped_column(Text)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    method: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
