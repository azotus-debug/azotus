import os
import re
import shutil
from pathlib import Path

def process_intouch_delivery(target_dir: str):
    source_dir = Path(target_dir)
    if not source_dir.exists():
        print(f"Error: Directory {source_dir} not found.")
        return

    # InTouch Naming Requirements:
    # 1. SRT: [I-CODE]_[DATE]_IS.srt (e.g., I2604_180415_IS.srt)
    # 2. MP4: [DATE]_[I-CODE]_lang_ice.mp4 (e.g., 180415_I2604_lang_ice.mp4)

    # First, let's load the PDF schedule mapping if available
    date_mapping = {}
    try:
        from PyPDF2 import PdfReader
        pdf_files = list(source_dir.glob("*.pdf"))
        if pdf_files:
            text = ""
            for pdf_file in pdf_files:
                for page in PdfReader(str(pdf_file)).pages:
                    text += page.extract_text() + "\n"
            
            # Extract mapping like: I-2604 01/25/26 180415 (Release ID -> Original Broadcast Date)
            for match in re.finditer(r"I-(\d{4})\s+\d{2}/\d{2}/\d{2}\s+(\d{6})", text):
                item_code = f"I{match.group(1)}"
                original_date = match.group(2)
                date_mapping[item_code] = original_date
            print(f"Loaded {len(date_mapping)} dates from PDF schedule(s).")
    except Exception as e:
        print(f"Note: Could not load PDF schedule for date lookup ({e}).")

    success_count = 0
    
    for file_path in source_dir.glob("*"):
        if file_path.is_file() and file_path.suffix.lower() in [".srt", ".mp4"]:
            
            # Find the I-Code in the filename
            code_match = re.search(r"(I\d{4})", file_path.name, re.IGNORECASE)
            if not code_match:
                continue
                
            item_code = code_match.group(1).upper()
            extension = file_path.suffix.lower()
            
            # Determine the 6-digit date
            date_code = date_mapping.get(item_code)
            if not date_code:
                # Try to extract from filename directly
                date_match = re.search(r"(\d{6})", file_path.name)
                if date_match:
                    date_code = date_match.group(1)
                else:
                    print(f"Warning: Could not find 6-digit date for {file_path.name}")
                    continue

            # Apply strict InTouch routing format based on file type
            if extension == ".srt":
                new_filename = f"{item_code}_{date_code}_IS.srt"
            elif extension == ".mp4":
                new_filename = f"{date_code}_{item_code}_lang_ice.mp4"
            else:
                continue
                
            new_filepath = source_dir / new_filename
            
            if str(file_path) != str(new_filepath) and not new_filepath.exists():
                print(f"Renaming:\n  [OLD] {file_path.name}\n  [NEW] {new_filename}")
                file_path.rename(new_filepath)
                success_count += 1

    print(f"\n✅ Successfully processed {success_count} InTouch delivery files.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        process_intouch_delivery(sys.argv[1])
    else:
        print("Usage: python3 package_intouch_delivery.py /path/to/delivery/folder")
