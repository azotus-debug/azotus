#!/bin/bash

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}🔄 Retrying Failed Translations...${NC}"

count=$(ls -1 99_ERRORS/*_SKELETON.json 2>/dev/null | wc -l)

if [ $count -eq 0 ]; then
    echo "   No files found in 99_ERRORS."
    exit 0
fi

# Move JSONs and MP3s back to the Brain's inbox
mv 99_ERRORS/*_SKELETON.json 2_READY_FOR_CLOUD/ 2>/dev/null
mv 99_ERRORS/*.mp3 2_READY_FOR_CLOUD/ 2>/dev/null

echo -e "   ✅ Moved ${GREEN}${count}${NC} files back to 2_READY_FOR_CLOUD."
echo "   The Brain will try them again immediately."
