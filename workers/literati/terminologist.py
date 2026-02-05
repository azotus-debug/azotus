"""
Terminologist Agent for Omega Literati.

Role:
- The "Naming Expert" and "Grammarian".
- Responsible for ensuring proper nouns are correctly declined in Icelandic.
- Interfaces with the BÍN (Beygingarlýsing íslensks nútímamáls) API.
- Falls back to LLM knowledge if BÍN fails.

Usage:
    from workers.literati.terminologist import Terminologist
    term = Terminologist()
    guide = term.generate_naming_guide(proper_nouns)
"""

import requests
import logging
import json
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

# Configure logging
logger = logging.getLogger("Terminologist")

BIN_API_URL = "https://bin.arnastofnun.is/api/ord/"

@dataclass
class DeclensionTable:
    lemma: str
    gender: str  # 'kk', 'kvk', 'hk'
    cases: Dict[str, str]  # { 'NFET': '...', 'ÞGFET': '...', ... }

class Terminologist:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "OmegaLiterati/1.0 (internal-tool)"
        })

    def lookup_bin(self, name: str) -> Optional[DeclensionTable]:
        """
        Look up a name in BÍN and return its declension table.
        """
        try:
            # Query BÍN API
            # precise=1 ensures we get exact matches first if possible
            url = f"{BIN_API_URL}{name}"
            response = self.session.get(url, timeout=5)
            
            if response.status_code != 200:
                logger.warning(f"BÍN API returned {response.status_code} for {name}")
                return None

            data = response.json()
            if not data:
                return None

            # Parse the first strict match (or first result)
            # The API returns a list of matching words.
            # Each word acts as a header for potential declension data.
            # We want proper nouns (Eignarnöfn) usually.
            
            # Simple heuristic: Take the first result that looks like a noun (no 'so' verbs etc)
            # BÍN returns: { 'ord': '...', 'guid': '...', ... } 
            # We might need to fetch the specific declension if not provided fully here.
            # Actually, `api/ord/{word}` returns basic info. We might need detailed bin info.
            
            # Let's simplify: Return the raw data for LLM processing or process basic cases here.
            # For this v1 implementation, we will trust the LLM to verify, 
            # but we pass the raw BÍN data if found.
            
            # If we want the full declension, we usually need the 'id' or 'guid' from the search
            # and then query `api/ord/{id}` if the first response was just a search result. 
            # However, `api/ord/{word}` usually gives a list of candidates.
            
            return data

        except Exception as e:
            logger.error(f"BÍN Lookup failed for {name}: {e}")
            return None

    def generate_naming_guide(self, glossary_terms: List[Dict]) -> str:
        """
        Takes a list of proper nouns (characters, places) and creates a 
        markdown naming guide for the Drafter/Translator.
        """
        guide_lines = ["# Naming & Declension Guide\n"]
        
        for term in glossary_terms:
            source = term.get("source_name")
            icelandic = term.get("icelandic_name") or source
            
            # Skip if only source is present and it looks like a common English name we want to keep?
            # Or always try to look it up.
            
            if not icelandic:
                continue

            guide_lines.append(f"## {source} -> {icelandic}")
            
            # Try BÍN lookup for the Icelandic version
            bin_data = self.lookup_bin(icelandic)
            
            if bin_data:
                # Format BÍN data nicely for the context window
                # We just dump the JSON for the LLM to interpret for now, 
                # as BÍN structure can be complex.
                compact_json = json.dumps(bin_data, ensure_ascii=False)
                guide_lines.append("### BÍN Data (Reference)")
                guide_lines.append(f"```json\n{compact_json}\n```")
                guide_lines.append("> **Note to Translator**: Use the above declension data to ensure grammatical correctness across all cases (NF, ÞF, ÞGF, EF).\n")
            else:
                guide_lines.append("> **Warning**: Term not found in BÍN. Please infer declension based on standard Icelandic grammar rules for this noun class.\n")
            
            # Add specific instructions if any
            if term.get("gender"):
                 guide_lines.append(f"- **Gender**: {term.get('gender')}")
            
            guide_lines.append("\n---\n")

        return "\n".join(guide_lines)

if __name__ == "__main__":
    # Simple test
    t = Terminologist()
    res = t.lookup_bin("Jón")
    print(json.dumps(res, indent=2, ensure_ascii=False))
