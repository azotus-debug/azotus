import os
import re
import shutil
from pathlib import Path

def process_intouch_batch():
    source_dir = Path("/Users/haukurhauksson/Azotus/4_DELIVERY/InTouch_Batch_2026-02-08")
    if not source_dir.exists():
        print(f"Error: Directory {source_dir} not found.")
        return

    # User's example:
    # Original: 180415_I2604_lang_ice.mp4
    # Expected: I2604_180415_IS.mp4

    # Parse the PDF schedule to build a mapping of I-Code -> DateCode
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        print("Error: PyPDF2 not installed. Run 'pip install PyPDF2'")
        return

    import re
    pdf_path = source_dir / "2026 Half Hour International TV Schedule 100225 .pdf"
    if not pdf_path.exists():
        print(f"Error: {pdf_path.name} not found.")
        return

    text = ""
    for page in PdfReader(str(pdf_path)).pages:
        text += page.extract_text() + "\n"

    # Regex to find lines like: I-2604 01/25/26 ...
    # Group 1: I-Code (e.g., 2604)
    # Group 2: MM/DD/YY (e.g., 01/25/26)
    schedule_pattern = re.compile(r"I-(\d{4})\s+(\d{2})/(\d{2})/(\d{2})")
    
    date_mapping = {}
    for match in schedule_pattern.finditer(text):
        item_code = f"I{match.group(1)}" # I2604
        # Convert MM/DD/YY to YYMMDD (e.g., 01/25/26 -> 260125, wait.. the user's example was 180415 for I2604... wait, InTouch dates can be weird)
        # Let's look at the user's example: 180415_I2604_lang_ice.mp4 -> I2604_180415_IS.mp4
        # Wait, if I2604 is 01/25/26 in the schedule, where did 180415 come from? 
        # Ah, 180415 might be the ORIGINAL US recording date (YYMMDD = 2018 April 15).
        # We need to extract the original recording date from the schedule!
        pass

    # Let's inspect the PDF schedule text format again.
    # Line format: I-2601 01/04/26 960714_MI146 Servanthood ...
    # This means:
    #   Release ID: I-2601
    #   Broadcast Date: 01/04/26
    #   Original ID/Date: 960714_MI146 (where 960714 is YYMMDD of original recording)
    
    # Wait, the example: `180415_I2604` -> The schedule probably has `I-2604 01/25/26 180415...`
    # Let's parse BOTH dates and map them.
    for match in re.finditer(r"I-(\d{4})\s+\d{2}/\d{2}/\d{2}\s+(\d{6})", text):
        item_code = f"I{match.group(1)}"
        original_date = match.group(2)
        date_mapping[item_code] = original_date
        
    print(f"Loaded {len(date_mapping)} dates from the schedule.")
    
    success_count = 0
    for file_path in source_dir.glob("*"):
        if file_path.is_file() and file_path.suffix.lower() in [".srt", ".mp4"]:
            # Find the I-Code in the filename
            code_match = re.search(r"(I\d{4})", file_path.name, re.IGNORECASE)
            if not code_match:
                continue
                
            item_code = code_match.group(1).upper()
            extension = file_path.suffix.lower()
            
            # If we don't have the date code, try to look it up
            date_code = date_mapping.get(item_code)
            
            if not date_code:
                # Fallback: Maybe the filename already HAS a 6-digit date?
                date_match = re.search(r"(\d{6})", file_path.name)
                if date_match:
                    date_code = date_match.group(1)
                else:
                    print(f"Warning: Could not find date code for {file_path.name}")
                    continue
                    
            new_filename = f"{item_code}_{date_code}_IS{extension}"
            new_filepath = source_dir / new_filename
            
            if str(file_path) != str(new_filepath) and not new_filepath.exists():
                print(f"Renaming:\n  [OLD] {file_path.name}\n  [NEW] {new_filename}")
                file_path.rename(new_filepath)
                success_count += 1


    print(f"\n✅ Successfully renamed {success_count} files in the InTouch Batch.")

if __name__ == "__main__":
    process_intouch_batch()
