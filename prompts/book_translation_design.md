# Omega Literati: Book Translation Design Document

> English → Icelandic Literary Translation System  
> Target: Christian ministry content with publication-quality output

---

## Table of Contents

1. [Translation Prompts](#translation-prompts)
   - [Step 1: Literary Translator (Gemini)](#step-1-literary-translator-gemini-3-pro)
   - [Step 2: Theological Reviewer (Gemini)](#step-2-theological-reviewer-gemini-3-pro)
   - [Step 3: Literary Polish (Claude)](#step-3-literary-polish-editor-claude-opus)
2. [Glossary Extraction Logic](#glossary-extraction-logic)
3. [UX Wireframe](#ux-wireframe-book-editor)

---

## Translation Prompts

### Step 1: Literary Translator (Gemini 3 Pro)

**Purpose:** Create a faithful, contextually-aware base translation that preserves meaning, tone, and narrative flow.

#### System Prompt

```
You are an expert English-to-Icelandic literary translator specializing in Christian ministry content. Your task is to create a faithful base translation that:

1. PRESERVES the author's voice, tone, and rhetorical style
2. MAINTAINS theological accuracy (do not alter doctrine or meaning)
3. USES natural Icelandic sentence structure (not word-for-word English calques)
4. APPLIES correct Icelandic grammar including:
   - Proper case declensions (nefnifall, þolfall, þágufall, eignarfall)
   - Correct grammatical gender agreement
   - Natural word order (V2 rule in main clauses)
5. CONSULTS the provided glossary for consistent terminology

## Context Available:
- **Book Glossary**: Established translations for names, places, and key terms
- **Previous Chapter Summary**: For narrative continuity
- **Style Guide**: Author's voice characteristics

## Translation Approach:
- Favor meaning equivalence over literal translation
- Adapt idioms to Icelandic equivalents (don't translate literally)
- Keep Scripture references in standard Icelandic Bible format
- Preserve paragraph structure and dialogue formatting

## Output Format:
Return ONLY the Icelandic translation. Do not include commentary, notes, or the original English. Preserve all formatting (paragraphs, dialogue markers, emphasis).
```

#### User Prompt Template

```
## Book Information
**Title**: {{book_title}}
**Author**: {{author}}
**Chapter**: {{chapter_number}} - {{chapter_title}}

## Glossary (Use These Translations)
{{glossary_json}}

## Previous Chapter Context
{{previous_chapter_summary}}

## Source Text to Translate
{{source_text}}

---
Translate the above chapter into Icelandic, following all guidelines.
```

#### Quality Guardrails

- **Max output tokens**: Set to 1.5x source word count (Icelandic is often more compact)
- **Temperature**: 0.3 (consistent but not robotic)
- **Validation**: Check that all glossary terms appear with correct translations
- **Red flags**: English words remaining, case mismatches, broken sentences

---

### Step 2: Theological Reviewer (Gemini 3 Pro)

**Purpose:** Ensure doctrinal accuracy, correct Scripture handling, and alignment with ministry voice.

#### System Prompt

```
You are a theological reviewer for Icelandic Christian literature. Your task is to review an AI-generated translation and ensure:

1. DOCTRINAL ACCURACY
   - Core theological concepts are translated correctly
   - No meaning drift that could alter doctrine
   - Trinity, salvation, grace, faith terms are precise

2. SCRIPTURE REFERENCES
   - Verse citations use Icelandic format (e.g., "Jóh 3:16" not "John 3:16")
   - Quoted Scripture matches the standard Icelandic Bible (1981/2007 translation)
   - Book names use Icelandic equivalents (Mattheusar, Markúsar, Lúkasar, Jóhannesar)

3. MINISTRY VOICE
   - Matches the warm, pastoral tone appropriate for ministry content
   - Avoids overly academic or cold theological language
   - Maintains accessibility for general Icelandic Christian readers

4. TERMINOLOGY CONSISTENCY
   - Uses established terms from the ministry glossary
   - Key phrases are translated consistently throughout

## Review Process:
1. Read the original English for full context
2. Review the Icelandic translation paragraph by paragraph
3. Make minimal corrections - preserve the translator's style
4. Only change what is theologically necessary or factually incorrect

## Output Format:
Return the CORRECTED Icelandic text. If a passage required significant changes, add a brief [Reviewer Note: ...] at the end explaining the theological reasoning.
```

#### User Prompt Template

```
## Original English
{{source_text}}

## Icelandic Translation (Step 1)
{{step1_translation}}

## Ministry Glossary
{{glossary_json}}

## Icelandic Bible Book Names Reference
Mattheusar (Matthew), Markúsar (Mark), Lúkasar (Luke), Jóhannesar (John),
Rómverja (Romans), 1./2. Korinþubréf (Corinthians), Galatabréf (Galatians),
Efesíubréf (Ephesians), Filipíbréf (Philippians), Kólossubréf (Colossians),
1./2. Þessalóníkubréf (Thessalonians), Hebréabréf (Hebrews), Jakobsbréf (James),
1./2. Pétursbréf (Peter), Opinberunarbókin (Revelation), Sálmar (Psalms),
Orðskviðir (Proverbs), Jesaja (Isaiah), Jeremía (Jeremiah)

---
Review and correct the translation for theological accuracy.
```

#### Quality Guardrails

- **Temperature**: 0.2 (conservative, minimal unnecessary changes)
- **Validation**: Scripture references must match Icelandic format
- **Track changes**: Log any [Reviewer Note] entries for human review
- **Red flags**: Doctrinal terms translated inconsistently, English Scripture citations

---

### Step 3: Literary Polish Editor (Claude Opus)

**Purpose:** Achieve publication-ready Icelandic prose with perfect grammar, natural rhythm, and emotional resonance.

#### System Prompt

```
You are an elite Icelandic literary editor with deep expertise in:
- Modern Icelandic grammar and morphology
- Literary style and prose rhythm
- The Icelandic literary tradition
- Christian devotional writing conventions

Your task is the final polish of an Icelandic translation. The content is theologically vetted - your focus is LINGUISTIC EXCELLENCE.

## Editing Priorities (in order):

### 1. Grammar Mastery
- Correct ALL case, gender, and number agreement errors
- Fix verb conjugations (person, number, tense, mood)
- Ensure proper definitie/indefinite article usage
- Apply correct word order (especially V2 in main clauses)

### 2. Morphological Precision
- Verify noun declensions across all 4 cases
- Check adjective agreement with nouns
- Ensure pronoun case matches grammatical function
- Handle irregular forms correctly (vera, fara, gera, etc.)

### 3. Natural Icelandic Flow
- Eliminate awkward constructions calqued from English
- Use natural Icelandic phrasing and collocations
- Vary sentence length for rhythm
- Employ appropriate discourse markers (því, þá, hins vegar, enda)

### 4. Literary Excellence
- Choose precise, evocative vocabulary over generic words
- Create smooth paragraph transitions
- Maintain consistent register (formal devotional, not casual)
- Preserve the author's emotional resonance

### 5. Icelandic Authenticity
- Prefer native Icelandic words over recent loanwords where natural
- Use traditional Icelandic expressions appropriately
- Ensure the text "sounds Icelandic" to a native reader

## Output Format:
Return ONLY the polished Icelandic text. Preserve all formatting. The output should be publication-ready with zero grammatical errors.
```

#### User Prompt Template

```
## Book Context
**Title**: {{book_title}}
**Author**: {{author}}  
**Chapter**: {{chapter_number}}
**Style**: {{style_guide_notes}}

## Character Voice Notes (if applicable)
{{character_bible_excerpt}}

## Icelandic Text to Polish
{{step2_theology_review}}

---
Polish this text to publication-ready Icelandic. Focus on grammar perfection and natural literary flow.
```

#### Quality Guardrails

- **Temperature**: 0.4 (allow creative word choice while maintaining accuracy)
- **Max tokens**: Match input length (polishing shouldn't expand text significantly)
- **Validation**: 
  - Run through Icelandic spell-check
  - Verify no English words remain
  - Check sentence count matches approximately
- **Red flags**: Major content changes, significant length deviation, style inconsistency

---

## Glossary Extraction Logic

### Purpose

Automatically extract and structure terminology from English source text to ensure translation consistency across all chapters.

### Glossary JSON Schema

```json
{
  "book_id": "book_abc123",
  "version": 1,
  "last_updated": "2026-01-10T16:00:00Z",
  
  "characters": [
    {
      "english_name": "Pastor Michael",
      "icelandic_name": "Mikael prestur",
      "type": "person",
      "gender": "masculine",
      "notes": "Main character, warm pastoral voice",
      "speech_patterns": {
        "register": "formal_pastoral",
        "catchphrases": ["My dear friends", "Let us remember"],
        "icelandic_equivalents": ["Kæru vinir", "Minnumst þess"]
      }
    }
  ],
  
  "places": [
    {
      "english_name": "Grace Community Church",
      "icelandic_name": "Náðarkirkjan",
      "type": "institution",
      "notes": "Keep as proper noun, no article"
    }
  ],
  
  "theological_terms": [
    {
      "english": "justification",
      "icelandic": "réttlæting",
      "context": "Pauline doctrine of being made right with God",
      "related_terms": ["righteousness/réttlæti", "faith/trú"]
    },
    {
      "english": "sanctification",
      "icelandic": "helgun",
      "context": "Process of becoming holy",
      "related_terms": ["holiness/heilagleiki", "Spirit/Andi"]
    },
    {
      "english": "atonement",
      "icelandic": "friðþæging",
      "context": "Christ's sacrificial work",
      "related_terms": ["sacrifice/fórn", "blood/blóð", "cross/kross"]
    },
    {
      "english": "grace",
      "icelandic": "náð",
      "context": "Unmerited favor from God",
      "related_terms": ["mercy/miskunn", "gift/gjöf"]
    },
    {
      "english": "salvation",
      "icelandic": "hjálpræði",
      "context": "Deliverance from sin",
      "related_terms": ["saved/frelsaður", "Savior/Frelsari"]
    }
  ],
  
  "ministry_phrases": [
    {
      "english": "born again",
      "icelandic": "endurfæddur",
      "context": "Spiritual rebirth (John 3)"
    },
    {
      "english": "the Word of God",
      "icelandic": "orð Guðs",
      "context": "Scripture reference"
    },
    {
      "english": "the Lord",
      "icelandic": "Drottinn",
      "context": "Divine title, always capitalized"
    }
  ]
}
```

### Extraction Heuristics

#### 1. Proper Noun Detection

```python
def extract_proper_nouns(text: str) -> list:
    """
    Extract proper nouns for glossary population.
    
    Heuristics:
    1. Capitalized words not at sentence start
    2. Title + Name patterns (Pastor John, Sister Mary)
    3. Multi-word capitalized sequences (Grace Community Church)
    4. Repeated capitalized terms (likely important names)
    """
    patterns = [
        # Title + Name
        r'\b(Pastor|Father|Sister|Brother|Reverend|Dr\.?|Mr\.?|Mrs\.?|Miss)\s+([A-Z][a-z]+)',
        
        # Place names (often multi-word)
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s+(?:Church|Chapel|Ministry|Center|School)',
        
        # Standalone capitalized (not sentence start)
        r'(?<=[a-z]\.\s)([A-Z][a-z]{2,})',
        r'(?<=[a-z],\s)([A-Z][a-z]{2,})',
        r'(?<=[a-z]\s)([A-Z][a-z]{2,})',
    ]
    
    # Frequency threshold: terms appearing 3+ times are likely important
    # Return sorted by frequency descending
```

#### 2. Theological Term Detection

```python
THEOLOGICAL_MARKERS = {
    # Core doctrine terms
    'justification', 'sanctification', 'atonement', 'redemption', 'propitiation',
    'regeneration', 'reconciliation', 'imputation', 'glorification',
    
    # Trinity terms
    'Trinity', 'Godhead', 'incarnation', 'deity', 'divine nature',
    
    # Salvation terms  
    'salvation', 'grace', 'faith', 'repentance', 'forgiveness', 'mercy',
    
    # Eschatology
    'rapture', 'tribulation', 'millennium', 'resurrection', 'eternal life',
    
    # Church terms
    'baptism', 'communion', 'eucharist', 'sacrament', 'ordination',
    
    # Scripture patterns
    r'\b(Gospel|Epistle|Testament|Scripture|Word of God)\b',
    r'\b(Pharisee|Sadducee|Gentile|apostle|disciple)\b',
}

def extract_theological_terms(text: str) -> list:
    """
    Identify theological vocabulary requiring careful translation.
    
    Returns terms with surrounding context (±20 words) for translator reference.
    """
```

#### 3. Ministry Phrase Detection

```python
MINISTRY_PHRASE_PATTERNS = [
    # Common evangelical phrases
    r'born again',
    r'accept(ed|ing)?\s+(Christ|Jesus|the Lord)',
    r'personal (relationship|Savior)',
    r'walk(ing)?\s+with\s+(God|Christ|the Lord)',
    r'(share|sharing)\s+(the Gospel|your faith|my testimony)',
    r'(prayer|quiet)\s+time',
    r'(Bible|Scripture)\s+study',
    r'(small|life|home)\s+group',
    r'(spiritual|faith)\s+journey',
    r'(called|calling)\s+to',
    
    # Repeated phrases unique to this author
    # Detected by: exact phrase appearing 5+ times
]
```

#### 4. Character Speech Pattern Detection

```python
def analyze_character_voice(name: str, dialogue_samples: list) -> dict:
    """
    Analyze how a character speaks for translation consistency.
    
    Returns:
    - register: formal/informal/pastoral/academic
    - catchphrases: repeated expressions
    - sentence_patterns: typical sentence structures
    - vocabulary_level: simple/moderate/complex
    """
```

### Edge Cases to Handle

| Case | Example | Handling |
|------|---------|----------|
| Biblical names | "John the Baptist" | Use established Icelandic: "Jóhannes skírari" |
| Modern names | "Pastor Steve" | Icelandicize if possible: "Stefán prestur" |
| Place names | "Jerusalem" | Use Icelandic: "Jerúsalem" |
| Made-up places | "Graceville Church" | Translate meaning: "Náðarbæjarkirkjan" |
| Acronyms | "VBS" (Vacation Bible School) | Expand and translate or keep with explanation |
| Untranslatable | Specific cultural references | Add translator note for context |

---

## UX Wireframe: Book Editor

### Overview

The Book Editor is a chapter-focused translation interface with split-view editing, glossary integration, and pipeline stage tracking.

### Layout Structure

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  📚 Omega Literati                                    [User] [Settings] [?]  │
├────────────────────┬─────────────────────────────────────────────────────────┤
│                    │                                                         │
│  BOOK: {{title}}   │  Chapter 3: The Path Forward                    [💾][▶]│
│  ─────────────────│  ───────────────────────────────────────────────────────│
│                    │                                                         │
│  📖 Chapters       │  ┌─────────────────────┐  ┌─────────────────────┐      │
│  ──────────────   │  │    🇬🇧 ENGLISH       │  │    🇮🇸 ICELANDIC     │      │
│  ✅ 1. Intro      │  │    (Source)          │  │    (Translation)     │      │
│  ✅ 2. Beginning  │  ├─────────────────────┤  ├─────────────────────┤      │
│  🔄 3. The Path ◀ │  │                     │  │                     │      │
│  ⏳ 4. Journey    │  │ "My dear friends,"  │  │ „Kæru vinir,"       │      │
│  ○  5. Arrival    │  │ Pastor Michael      │  │ hóf Mikael prestur  │      │
│  ○  6. Home       │  │ began, his voice    │  │ mál sitt, röddin    │      │
│                    │  │ warm with the       │  │ hlý af gleði        │      │
│  ──────────────   │  │ joy of the          │  │ morgunsins sem      │      │
│  📊 Progress: 42% │  │ morning...          │  │ nýhafði...          │      │
│  ██████░░░░░░░░   │  │                     │  │                     │      │
│                    │  │                     │  │ [Edit Mode 📝]      │      │
│  ──────────────   │  │                     │  │                     │      │
│  📚 Glossary      │  └─────────────────────┘  └─────────────────────┘      │
│  ──────────────   │                                                         │
│  > Characters (4) │  ─────────────────────────────────────────────────────  │
│  > Places (3)     │  PIPELINE STAGE                                         │
│  > Theology (12)  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  > Phrases (8)    │  │ Step 1   │→ │ Step 2   │→ │ Step 3   │→ │ Final  │  │
│                    │  │ Literary │  │ Theology │  │ Polish   │  │ Review │  │
│  ──────────────   │  │ ✅ Done  │  │ 🔄 Active│  │ ○ Pending│  │ ○      │  │
│  🔒 Locked by:    │  └──────────┘  └──────────┘  └──────────┘  └────────┘  │
│  gemini-worker    │                                                         │
│                    │  [◀ Prev Chapter]            [Run Step 2 ▶] [Next ▶]   │
└────────────────────┴─────────────────────────────────────────────────────────┘
```

### Component Descriptions

#### 1. Header Bar
- **Logo/Title**: "Omega Literati" branding
- **User menu**: Account, logout
- **Settings**: Model preferences, API keys
- **Help**: Documentation link

#### 2. Sidebar: Chapter Navigator

```
┌────────────────────┐
│  📖 Chapters       │
│  ──────────────   │
│  [Status] [Title]  │
│  ✅ = Complete     │
│  🔄 = In Progress  │
│  ⏳ = Queued       │
│  ○  = Not Started  │
│  ◀  = Current      │
├────────────────────┤
│  📊 Progress: 42%  │
│  ▓▓▓▓▓▓░░░░░░░░   │
│  7/15 chapters     │
└────────────────────┘
```

**Interactions:**
- Click chapter → loads in main view
- Drag to reorder (if needed)
- Right-click → Mark complete, Reset, Delete

#### 3. Sidebar: Glossary Panel

```
┌────────────────────┐
│  📚 Glossary       │
│  [Search... 🔍]    │
├────────────────────┤
│  ▼ Characters (4)  │
│    Pastor Michael  │
│    → Mikael prestr │
│    Sister Grace    │
│    → Nöður systir  │
├────────────────────┤
│  ▶ Places (3)      │
│  ▼ Theology (12)   │
│    justification   │
│    → réttlæting    │
│    grace           │
│    → náð           │
│  ▶ Phrases (8)     │
├────────────────────┤
│  [+ Add Term]      │
│  [⬆ Import JSON]   │
└────────────────────┘
```

**Interactions:**
- Expand/collapse categories
- Click term → highlight in source text
- Edit term inline
- Import/export glossary JSON

#### 4. Main View: Split Editor

**Left Panel: English Source (Read-Only)**
```
┌─────────────────────────────┐
│  🇬🇧 ENGLISH (Source)        │
│  Word count: 2,847          │
├─────────────────────────────┤
│                             │
│  [Rendered text with        │
│   preserved formatting]     │
│                             │
│  Glossary terms are         │
│  **highlighted** in yellow  │
│                             │
│  Click any word → shows     │
│  glossary entry if exists   │
│                             │
└─────────────────────────────┘
```

**Right Panel: Icelandic Translation (Editable)**
```
┌─────────────────────────────┐
│  🇮🇸 ICELANDIC (Translation) │
│  Word count: 2,612          │
│  [View: Final ▼] [Edit 📝]  │
├─────────────────────────────┤
│                             │
│  [Editable text area with   │
│   rich text formatting]     │
│                             │
│  Tab between versions:      │
│  - Step 1 Draft             │
│  - Step 2 Reviewed          │
│  - Step 3 Polished          │
│  - Final (Human Edited)     │
│                             │
│  Diff view available to     │
│  compare versions           │
│                             │
└─────────────────────────────┘
```

#### 5. Pipeline Stage Indicator

```
┌──────────────────────────────────────────────────────────────┐
│  TRANSLATION PIPELINE                                        │
│                                                              │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌─────┐ │
│  │  Step 1  │ ──▶ │  Step 2  │ ──▶ │  Step 3  │ ──▶ │Final│ │
│  │ Literary │     │ Theology │     │  Polish  │     │     │ │
│  │  ✅ Done │     │ 🔄 Active│     │ ○ Pending│     │ ○   │ │
│  │ Gemini   │     │ Gemini   │     │ Claude   │     │Human│ │
│  │ 2m 34s   │     │ Running  │     │ —        │     │ —   │ │
│  └──────────┘     └──────────┘     └──────────┘     └─────┘ │
│                                                              │
│  [⏸ Pause Pipeline]  [▶ Run Next Step]  [⟲ Re-run Step 2]   │
└──────────────────────────────────────────────────────────────┘
```

**Stage States:**
- `✅` Complete (green) - shows duration
- `🔄` Active (blue) - shows progress spinner
- `⏳` Queued (yellow) - waiting for previous step
- `○` Pending (gray) - not started
- `❌` Failed (red) - shows error message

#### 6. Footer: Navigation & Actions

```
┌──────────────────────────────────────────────────────────────┐
│  [◀ Ch 2: Beginning]     [Run Theology Review ▶]     [Ch 4 ▶]│
│                                                              │
│  💾 Auto-saved 12s ago   |   🔒 Locked by: you   |   [Unlock]│
└──────────────────────────────────────────────────────────────┘
```

### Modal: Run Translation Step

```
┌─────────────────────────────────────────────────────┐
│  Run Step 2: Theological Review                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Model: Gemini 3 Pro                               │
│  Input: Step 1 translation (2,612 words)           │
│  Estimated time: ~3 minutes                        │
│  Estimated cost: $0.08                             │
│                                                     │
│  ☑ Include glossary context                        │
│  ☑ Include previous chapter summary                │
│  ☐ Force re-run (overwrite existing)               │
│                                                     │
│  [Cancel]                        [Run Translation] │
└─────────────────────────────────────────────────────┘
```

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+S` | Save current edits |
| `Cmd+Enter` | Run next pipeline step |
| `Cmd+[` / `]` | Previous/Next chapter |
| `Cmd+G` | Toggle glossary panel |
| `Cmd+D` | Show diff between versions |
| `Cmd+1/2/3/4` | Switch to Step 1/2/3/Final view |
| `Esc` | Exit edit mode |

### Responsive Behavior

**Desktop (1200px+):** Full three-column layout as shown  
**Tablet (768-1199px):** Sidebar collapses to icons, expands on hover  
**Mobile (< 768px):** Stack layout - chapter list → translation view → glossary (swipe between)

### Color Palette

| Element | Color | Usage |
|---------|-------|-------|
| Primary | `#1a73e8` | Buttons, links, active states |
| Success | `#34a853` | Completed stages, saved state |
| Warning | `#fbbc05` | Queued, needs attention |
| Error | `#ea4335` | Failed, errors |
| Background | `#f8f9fa` | Page background |
| Surface | `#ffffff` | Cards, panels |
| Text Primary | `#202124` | Main content |
| Text Secondary | `#5f6368` | Labels, metadata |

---

## Implementation Notes

### API Endpoints Required

```
POST   /api/books                      # Create book project
GET    /api/books                      # List all books
GET    /api/books/{id}                 # Get book with chapters
DELETE /api/books/{id}                 # Delete book

POST   /api/books/{id}/chapters        # Add chapter
GET    /api/books/{id}/chapters/{ch}   # Get chapter detail
PATCH  /api/books/{id}/chapters/{ch}   # Update translation

POST   /api/books/{id}/chapters/{ch}/run-step  # Run pipeline step
POST   /api/books/{id}/chapters/{ch}/lock      # Lock for editing
DELETE /api/books/{id}/chapters/{ch}/lock      # Unlock

GET    /api/books/{id}/glossary        # Get glossary
PATCH  /api/books/{id}/glossary        # Update glossary
POST   /api/books/{id}/extract-glossary  # Auto-extract from source
```

### Database Schema Reference

Tables already implemented in `omega_db.py`:
- `book_projects` - Book metadata, glossary JSON, progress
- `book_chapters` - Chapter content, translation stages, locks

---

*Document version: 1.0*  
*Created: 2026-01-10*  
*For: Omega Literati Book Translation System*
