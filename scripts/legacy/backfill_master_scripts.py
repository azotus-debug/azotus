import omega_db


def main() -> None:
    omega_db.ensure_schema()
    tracks = omega_db.get_all_tracks()
    if not tracks:
        print("No tracks found. Nothing to backfill.")
        return

    script_cache = {}
    updated = 0

    for track in tracks:
        program_id = track.get("program_id")
        language_code = track.get("language_code")
        if not program_id or not language_code:
            continue

        key = (program_id, language_code)
        master_script_id = script_cache.get(key)
        if not master_script_id:
            master_script_id = omega_db.ensure_master_script(
                program_id=program_id,
                language_code=language_code,
                language_name=track.get("language_name"),
            )
            script_cache[key] = master_script_id

        updates = {}
        if not track.get("master_script_id"):
            updates["master_script_id"] = master_script_id
        if not track.get("output_version"):
            updates["output_version"] = "1.0"
        if track.get("pending_resync") is None:
            updates["pending_resync"] = False

        if updates:
            omega_db.update_track(track["id"], **updates)
            updated += 1

    print(f"Backfill complete. Updated {updated} tracks.")


if __name__ == "__main__":
    main()
