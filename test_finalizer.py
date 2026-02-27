import json
from pathlib import Path
from workers import finalizer

def main():
    job_id = "i2608_iceland-20260219T220753150282Z"
    
    # 1. Load the raw cloud output (the raw JSON before finalizer got hold of it)
    raw_path = Path("/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE") / f"{job_id}_APPROVED.json"
    if not raw_path.exists():
        # See if there's a backup before the hotfix
        raw_path = Path("/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE") / f"{job_id}_APPROVED.backup-before-hotfix.json"
        
    print(f"Loading raw payload from: {raw_path}")
    
    with open(raw_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
        
    raw_segments = payload.get("segments", [])
    if not raw_segments:
        print("No segments found in payload!")
        return
        
    print(f"Loaded {len(raw_segments)} raw segments.")
    
    # 2. Run the deterministic finalizer (BBC approach)
    # We pass the segments through the same entrypoint the cloud worker uses
    print("\nRunning finalizer formatting pass...")
    
    # We need to temporarily force OMEGA_SIMPLIFIED_FINALIZER off or modify the arguments 
    # if it's currently hardcoded to skip.
    # We will simulate the new logic directly to see the effect.
    
    # Let's apply our 3 rules explicitly to the raw segments for this test:
    
    # Rule 1: The Micro-Flash Eliminator
    merged_segments = []
    i = 0
    while i < len(raw_segments):
        current = raw_segments[i]
        duration = current['end'] - current['start']
        
        # If it's a micro-flash (< 1.0s) and there is a next segment
        if duration < 1.0 and i < len(raw_segments) - 1:
            next_seg = raw_segments[i+1]
            
            # Merge them
            merged = {
                'start': current['start'],
                'end': next_seg['end'],
                'text': current['text'].strip() + " " + next_seg['text'].strip(),
                'speaker': current.get('speaker', 'SPEAKER_00')
            }
            merged_segments.append(merged)
            print(f" MERGED Micro-flash ({duration:.2f}s): '{current['text']}' -> '{next_seg['text']}'")
            i += 2  # Skip the next segment since we merged it
        else:
            merged_segments.append(current)
            i += 1
            
    # Rule 2: The Dangling Preposition Fixer
    dangling_words = ['í', 'á', 'um', 'til', 'við', 'frá', 'með', 'og', 'eða', 'en']
    fixed_dangling = []
    
    for i in range(len(merged_segments)):
        current = merged_segments[i]
        text_parts = current['text'].split()
        
        if not text_parts:
            fixed_dangling.append(current)
            continue
            
        last_word = text_parts[-1].lower()
        
        # If the last word is a dangling word and there's a next segment
        if last_word in dangling_words and i < len(merged_segments) - 1:
            # Move the word
            next_seg = merged_segments[i+1]
            
            # Remove from current
            current['text'] = " ".join(text_parts[:-1])
            
            # Add to next (maintaining capitalisation if needed)
            if current['text'].endswith(('.', '!', '?')):
                next_seg['text'] = last_word.capitalize() + " " + next_seg['text']
            else:
                next_seg['text'] = last_word + " " + next_seg['text']
                
            print(f" MOVED Dangling '{last_word}': Subtitle {i+1} -> {i+2}")
            
        fixed_dangling.append(current)
        
    # Rule 3: The Sentence Boundary Fixer (Capitalization)
    for i in range(1, len(fixed_dangling)):
        prev = fixed_dangling[i-1]
        current = fixed_dangling[i]
        
        if not prev['text'] or not current['text']:
            continue
            
        # If previous segment didn't end with sentence-ending punctuation
        if not prev['text'].strip().endswith(('.', '!', '?', '."', '?"', '!"')):
            # The current segment should probably start lowercase
            first_word = current['text'].split()[0]
            
            # Simple check for proper nouns in Icelandic (very basic for this test)
            proper_nouns = ['Guð', 'Guðs', 'Drottinn', 'Jesús', 'Kristur', 'Heilagur', 'Andi']
            if first_word not in proper_nouns and first_word[0].isupper():
                current['text'] = current['text'][0].lower() + current['text'][1:]
                print(f" LOWERCASED Continuation: '{current['text'][:20]}...'")

    # 3. Save out the new SRT for review
    out_srt_path = Path("/Users/haukurhauksson/Azotus/4_DELIVERY/SRT") / f"DONE_{job_id}_BBC_FORMAT.srt"
    
    with open(out_srt_path, 'w', encoding='utf-8') as f:
        for i, event in enumerate(fixed_dangling):
            start = finalizer.format_timestamp(event['start'])
            end = finalizer.format_timestamp(event['end'])
            f.write(f"{i+1}\n{start} --> {end}\n{event['text']}\n\n")
            
    print(f"\nSaved newly formatted SRT to: {out_srt_path}")

if __name__ == "__main__":
    main()
