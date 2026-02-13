# Summarization Prompt Templates

## Voice-to-Summary Paste Tool

**Date**: 2026-02-13
**Author**: Prompt Engineer
**Target Model**: OpenAI GPT-4o-mini
**Target Language**: German (primary), language-matching for others

---

## 1. Default Prompt: Clean Summary

This is the production prompt used in v0.2. Optimized for token efficiency (<120 system prompt tokens), German language fidelity, and output-only behavior.

### System Prompt

```
Du bist ein Textbereinigungsassistent. Du erhaeltst rohe Sprache-zu-Text-Transkriptionen.

Regeln:
1. Entferne Fuellwoerter (aehm, also, halt, sozusagen, quasi, ne, ja, genau).
2. Korrigiere Grammatik und Zeichensetzung.
3. Bei Selbstkorrekturen: behalte nur die beabsichtigte Aussage.
4. Kuerze den Text auf das Wesentliche, ohne Informationen zu verlieren.
5. Antworte NUR mit dem bereinigten Text. Keine Erklaerungen, keine Kommentare.
6. Antworte in derselben Sprache wie die Eingabe.
```

### User Prompt Template

```
{transcript}
```

### Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| model | gpt-4o-mini | Cost-effective, fast, good German quality |
| temperature | 0.3 | Consistent output, avoids robotic phrasing |
| max_tokens | 2048 | 2x expected max output for long recordings |
| top_p | 1.0 | Default, not needed with temperature control |

---

## 2. Prompt Variant: Professional

For formal business communication. Available in v1.0 via config.

### System Prompt

```
Schreibe diese gesprochene Transkription als professionellen, formellen Absatz um, geeignet fuer eine geschaeftliche E-Mail oder ein Dokument. Entferne alle gesprochenen Artefakte und Fuellwoerter. Bewahre alle Kernaussagen. Antworte NUR mit dem Ergebnis in derselben Sprache wie die Eingabe.
```

---

## 3. Prompt Variant: Concise

For ultra-short note-taking. Available in v1.0 via config.

### System Prompt

```
Komprimiere diese gesprochene Transkription auf die kuerzestmoegliche Version, die alle Fakten und Entscheidungen enthaelt. Verwende knappe, praegnante Sprache. Entferne alle Fuellwoerter, Wiederholungen und Absicherungen. Antworte NUR mit dem Ergebnis in derselben Sprache wie die Eingabe.
```

---

## 4. Test Evaluation: 5 Sample Transcripts

### Sample 1: Simple Note

**Input:**
"Also aehm ich wollte nur sagen dass das Meeting morgen um zehn Uhr stattfindet also nicht um neun wie urspruenglich geplant ja genau also zehn Uhr im grossen Konferenzraum."

**Expected Output:**
"Das Meeting findet morgen um zehn Uhr im grossen Konferenzraum statt, nicht um neun Uhr wie urspruenglich geplant."

**Analysis:** Filler words (also, aehm, ja, genau) removed. Self-correction (nicht um neun) preserved as clarification. Single clean sentence.

### Sample 2: Technical Discussion with English Terms

**Input:**
"Wir muessen den API Endpoint aendern weil der halt deprecated ist sozusagen also der alte Endpoint funktioniert noch aber ab naechster Woche halt nicht mehr und dann muessen wir quasi auf die neue Version migrieren Version drei."

**Expected Output:**
"Der API-Endpoint muss geaendert werden, da er ab naechster Woche deprecated ist. Die Migration auf Version 3 ist erforderlich."

**Analysis:** English tech terms (API Endpoint, deprecated) preserved. Filler (halt, sozusagen, quasi) removed. Consolidated into two clear sentences.

### Sample 3: Task Assignment

**Input:**
"Aehm also ich brauche von dir bis Freitag den Bericht fertig ja also den Quartalsbericht meine ich nicht den Monatsbericht den Quartalsbericht und aehm bitte auch die Grafiken aktualisieren die sind noch vom letzten Quartal."

**Expected Output:**
"Bitte den Quartalsbericht bis Freitag fertigstellen und die Grafiken aktualisieren, da diese noch vom letzten Quartal sind."

**Analysis:** Self-correction (nicht den Monatsbericht, den Quartalsbericht) resolved to final meaning. Filler removed. Clear imperative.

### Sample 4: Complex Multi-Point

**Input:**
"Also erstens muessen wir das Budget nochmal ueberpruefen weil da glaube ich ein Fehler drin ist zweitens aehm muss das Team informiert werden ueber die neuen Richtlinien und drittens ja genau drittens sollen wir einen Workshop planen fuer naechsten Monat zum Thema Datenschutz oder so."

**Expected Output:**
"Drei Punkte: 1. Budget ueberpruefen (moeglicher Fehler). 2. Team ueber die neuen Richtlinien informieren. 3. Workshop zum Thema Datenschutz fuer naechsten Monat planen."

**Analysis:** Structure preserved as numbered list. Filler removed. Hedging ("glaube ich", "oder so") cleaned to factual language.

### Sample 5: Very Short / Almost Empty

**Input:**
"Aehm ja also genau."

**Expected Output:**
"" (empty -- no substantive content)

**Analysis:** Transcript is entirely filler words. Output should be empty or near-empty. The pipeline should handle this gracefully (treat as empty transcript, do not paste).

---

## 5. Edge Case Handling

| Edge Case | Prompt Behavior |
|-----------|-----------------|
| Input is all filler words | Output is empty or near-empty |
| Input is in English | Output should be in English (rule 6: match language) |
| Input mixes German and English | Output preserves technical English terms within German text |
| Input is a single word | Output is that word, cleaned |
| Input is very long (>2000 chars) | Prompt still works; max_tokens limit prevents runaway |
| Input contains numbers/dates | Numbers and dates preserved exactly |
| Input contains proper nouns | Proper nouns preserved exactly |

---

## 6. Integration Notes for Developer

The developer should use these values when integrating the summarization:

```python
SUMMARIZE_SYSTEM_PROMPT = (
    "Du bist ein Textbereinigungsassistent. Du erhaeltst rohe "
    "Sprache-zu-Text-Transkriptionen.\n\n"
    "Regeln:\n"
    "1. Entferne Fuellwoerter (aehm, also, halt, sozusagen, quasi, ne, ja, genau).\n"
    "2. Korrigiere Grammatik und Zeichensetzung.\n"
    "3. Bei Selbstkorrekturen: behalte nur die beabsichtigte Aussage.\n"
    "4. Kuerze den Text auf das Wesentliche, ohne Informationen zu verlieren.\n"
    "5. Antworte NUR mit dem bereinigten Text. Keine Erklaerungen, keine Kommentare.\n"
    "6. Antworte in derselben Sprache wie die Eingabe."
)

SUMMARIZE_MODEL = "gpt-4o-mini"
SUMMARIZE_TEMPERATURE = 0.3
SUMMARIZE_MAX_TOKENS = 2048
SUMMARIZE_TIMEOUT_SECONDS = 15
```

The user prompt is simply the raw transcript text with no wrapping.
