# Architecture — diarioOficial-kmz

## Overview

This system extends **EBM_inteligencia_mercado** by adding geospatial KMZ generation
and enriched Telegram notifications for every terreno (lot) identified in the
Goiânia Diário Oficial.

## Data Flow

```
Goiânia Portal
    │
    ▼
[PDF download]
    │
    ▼
[Text extraction via pypdf]
    │
    ▼
[Gemini AI analysis]
    │  → JSON list of terrenos (interessado, local, decisao, endereco_kmz)
    ▼
[For each terreno]
    ├─ [AI address parsing — multi-model fallback]
    │       Gemini → Mistral Large → Mistral Small → DeepSeek V3 → ...
    │
    ├─ [Hierarchy CSV lookup] → lote IDs
    │
    ├─ [Supabase vertices fetch] → polygon coordinates
    │
    ├─ [KMZ generation via simplekml + geopandas]
    │       → unified polygon (union of all matched lotes)
    │       → centroid (lat, lon) calculation
    │       → .kmz file saved to kmz_outputs/
    │
    ├─ [KMZ integrity validation]
    │
    └─ [Telegram notification]
            → HTML message (interessado, local, decisao, centroid coords, PDF link)
            → KMZ document attachment
```

## Modules

| Module | Responsibility |
|--------|---------------|
| `src/monitor_diario_kmz.py` | Orchestration: fetch PDF, AI analysis, dispatch per terreno |
| `src/kmz_generator.py`      | Address parsing (AI), lote lookup, vertex fetch, KMZ build, centroid |
| `src/telegram_sender.py`    | Send text messages and document attachments to Telegram |

## Environment Variables

See `config.env` for the full list. Critical variables:

| Variable | Used by |
|----------|---------|
| `TELEGRAM_TOKEN` | telegram_sender |
| `TELEGRAM_CHAT_ID` | telegram_sender |
| `GOOGLE_API_KEY` | monitor_diario_kmz (Gemini) |
| `SUPABASE_URL` / `SUPABASE_KEY` | kmz_generator |
| `URL_BASE_LOTES` | kmz_generator (hierarchy CSV) |
| AI keys (`GEMINI_KEY`, `MISTRAL_KEY`, etc.) | kmz_generator (fallback chain) |

## AI Fallback Chain

The KMZ generator tries models in order until one succeeds:
1. Gemini 1.5 Flash
2. Mistral Large
3. Mistral Small
4. DeepSeek V3 (via SiliconFlow)

This ensures resilience when any single provider has downtime.

## KMZ Structure

Each KMZ file is a ZIP containing `doc.kml` with:
- A single `<Polygon>` element (union of all matched lotes)
- Name: `QD. {quadra} - {bairro} - lotes {lote_ids}`
- Style: semi-transparent cyan fill, blue outline

## Relationship to EBM_inteligencia_mercado

This repository is an **extension**, not a replacement. It preserves all original
scripts and adds a new integrated pipeline (`src/`). Original scripts remain
runnable independently.
