import sys
import json
import re
from pathlib import Path

def shift_timestamp(timestamp_str, offset_seconds):
    """Shifts a timestamp string (00:00:00,000) by offset."""
    # Handle SRT/VTT format (comma or dot)
    parts = re.split('[:,.]', timestamp_str)
    if len(parts) == 4:
        h, m, s, ms = map(int, parts)
        total_seconds = h * 3600 + m * 60 + s + ms / 1000.0
    elif len(parts) == 3: # potentially VTT without hours? or ASS
        logging.error(f"Unexpected format: {timestamp_str}")
        return timestamp_str
    
    new_total = max(0, total_seconds + offset_seconds)
    
    nh = int(new_total // 3600)
    rem = new_total % 3600
    nm = int(rem // 60)
    ns = int(rem % 60)
    nms = int((rem - nm * 60 - ns) * 1000)
    
    return f"{nh:02d}:{nm:02d}:{ns:02d},{nms:03d}"

def shift_ass_timestamp(timestamp_str, offset_seconds):
    """Shifts ASS timestamp (h:mm:ss.cc)."""
    parts = timestamp_str.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    
    total = h * 3600 + m * 60 + s
    new_total = max(0, total + offset_seconds)
    
    nh = int(new_total // 3600)
    rem = new_total % 3600
    nm = int(rem // 60)
    ns = rem % 60
    
    return f"{nh}:{nm:02d}:{ns:05.2f}"

def process_file(file_path, offset):
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {path}")
        return

    print(f"Processing {path.name} with offset {offset}s...")
    
    # 1. JSON
    if path.suffix == '.json':
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if 'segments' in data:
            for seg in data['segments']:
                seg['start'] += offset
                seg['end'] += offset
                if 'words' in seg:
                    for w in seg['words']:
                        w['start'] += offset
                        w['end'] += offset
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            print("  Updated JSON")

    # 2. SRT
    if path.suffix == '.srt':
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        def replace_time(match):
            return f"{shift_timestamp(match.group(1), offset)} --> {shift_timestamp(match.group(2), offset)}"
            
        new_content = re.sub(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})', replace_time, content)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            print("  Updated SRT")

    # 3. ASS
    if path.suffix == '.ass':
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        new_lines = []
        for line in lines:
            if line.startswith('Dialogue:'):
                # Dialogue: 0,0:00:02.40,0:00:07.42,Default,,0,0,0,,Text
                parts = line.split(',', 9)
                if len(parts) > 2:
                    parts[1] = shift_ass_timestamp(parts[1], offset)
                    parts[2] = shift_ass_timestamp(parts[2], offset)
                    new_lines.append(','.join(parts))
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
                
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            print("  Updated ASS")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 shift_subtitles.py <file_path_without_ext> <offset_seconds>")
        sys.exit(1)
        
    stem = sys.argv[1]
    offset = float(sys.argv[2])
    
    base_path = Path("2_VAULT/Data")
    
    # Locate files
    files = [
        base_path / f"{stem}_SKELETON_DONE.json",
        base_path / f"{stem}.srt",
        base_path / f"{stem}.ass",
        base_path / f"{stem}.vtt"
    ]
    
    for f in files:
        if f.exists():
            process_file(f, offset)
        else:
            # Try simplified name
            simple = base_path / f"{stem}_normalized.json"
            if simple.exists() and f.suffix == '.json':
                 process_file(simple, offset)
