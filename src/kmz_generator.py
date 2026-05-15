"""
kmz_generator.py
────────────────
Generates KMZ files from lote (lot) vertex data stored in Supabase.

Pipeline:
  1. Load bairro/quadra/lote hierarchy CSV from URL_BASE_LOTES.
  2. Use AI (multi-model fallback) to extract structured address data.
  3. Lookup matching lote IDs in the hierarchy CSV.
  4. Fetch polygon vertices from Supabase 'vertices' table.
  5. Build unified polygon and generate .kmz file.
  6. Return KMZ path + centroid (lat, lon).

Environment variables required (see config.env):
  SUPABASE_URL, SUPABASE_KEY, URL_BASE_LOTES
  ZAI_API_KEY, and optionally MISTRAL_KEY, SILICONFLOW_KEY, GROQ_KEY, etc.
"""

import os
import re
import json
import pandas as pd
import geopandas as gpd
import simplekml

from pathlib import Path
from shapely.geometry import Polygon
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── Column names in the hierarchy CSV ────────────────────────────────────────
COL_BAIRRO  = "nm_bai"
COL_QUADRA  = "nm_quadra"
COL_LOTE    = "nm_lote"
COL_ID_LOTE = "id_lote"

# ── API keys (loaded from environment) ───────────────────────────────────────
API_KEYS = {
    "ZAI_API_KEY":     os.getenv("ZAI_API_KEY"),
    "MISTRAL_KEY":     os.getenv("MISTRAL_KEY"),
    "SILICONFLOW_KEY": os.getenv("SILICONFLOW_KEY"),
    "GROQ_KEY":        os.getenv("GROQ_KEY"),
    "OPENROUTER_KEY":  os.getenv("OPENROUTER_KEY"),
    "GROK_KEY":        os.getenv("GROK_KEY"),
    "CEREBRAS_KEY":    os.getenv("CEREBRAS_KEY"),
}

# ── AI model fallback list ────────────────────────────────────────────────────
MODELS_TO_TRY = [
    {
        "label": "GLM-4.7 Flash (Z.AI)",
        "model": "glm-4.7-flash",
        "base_url": "https://api.z.ai/api/paas/v4/",
        "api_key_var": "ZAI_API_KEY",
    },
    {
        "label": "GLM-4.5 Flash (Z.AI)",
        "model": "glm-4.5-flash",
        "base_url": "https://api.z.ai/api/paas/v4/",
        "api_key_var": "ZAI_API_KEY",
    },
    {
        "label": "Mistral Large",
        "model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_var": "MISTRAL_KEY",
    },
    {
        "label": "Mistral Small",
        "model": "mistral-small-latest",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_var": "MISTRAL_KEY",
    },
    {
        "label": "DeepSeek V3",
        "model": "deepseek-ai/DeepSeek-V3",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_var": "SILICONFLOW_KEY",
    },
]

# ── Supabase client ───────────────────────────────────────────────────────────
_supabase_url = os.getenv("SUPABASE_URL")
_supabase_key = os.getenv("SUPABASE_KEY")
supabase = create_client(_supabase_url, _supabase_key) if _supabase_url and _supabase_key else None

# ── Output directory ──────────────────────────────────────────────────────────
KMZ_OUTPUT_DIR = Path(os.getenv("KMZ_OUTPUT_DIR", "kmz_outputs"))
KMZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ═════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _limpar_texto(texto: str) -> str:
    """Strip emojis and non-alphanumeric chars, keep text/numbers intact."""
    return re.sub(r"[^\w\s.,;:\-\/()ºª]", "", texto, flags=re.UNICODE).strip()


def _normalizar_numero(texto: str) -> str:
    """Normalize lote/quadra identifiers: remove prefix keywords, strip zeroes."""
    t = str(texto).upper().strip()
    t = re.sub(r"^(QD|QUADRA|Q|LT|LOTE|L)\.?\s*", "", t)
    return str(int(t)) if t.isdigit() else t


def _extrair_dados_com_ia(texto_usuario: str, bairros_oficiais: list[str]) -> dict:
    """
    Use AI (with multi-model fallback) to extract bairro, quadra, lotes from text.

    Returns dict: {"bairro": str, "quadra": str, "lotes": [str, ...]}
    Raises Exception if all models fail.
    """
    bairros_str = ", ".join(map(str, bairros_oficiais))
    prompt = (
        f'Extraia bairro, quadra e lotes. Use o nome EXATO do bairro da LISTA OFICIAL abaixo.\n'
        f'LISTA OFICIAL: {bairros_str}\n'
        f'TEXTO: "{texto_usuario}"\n'
        f'Retorne apenas JSON: {{"bairro": "NOME", "quadra": "0", "lotes": ["0"]}}'
    )

    for config in MODELS_TO_TRY:
        chave = API_KEYS.get(config["api_key_var"])
        if not chave:
            continue
        try:
            print(f"   🤖 Tentando com {config['label']}...")
            client = OpenAI(api_key=chave, base_url=config["base_url"])
            response = client.chat.completions.create(
                model=config["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as exc:
            print(f"   ⚠️  Erro com {config['label']}: {exc}")
            continue

    raise RuntimeError("❌ Todas as IAs falharam ao processar o endereço.")


def _buscar_ids_lotes(dados_ia: dict, df_lotes: pd.DataFrame) -> list[int]:
    """Cross-reference AI-extracted data with the hierarchy CSV to get lote IDs."""
    bairro_target = str(dados_ia["bairro"]).upper()
    quadra_target = _normalizar_numero(dados_ia["quadra"])

    df_lotes["_busca_quadra"] = df_lotes[COL_QUADRA].apply(_normalizar_numero)
    filtro = df_lotes[
        (df_lotes[COL_BAIRRO].str.upper() == bairro_target)
        & (df_lotes["_busca_quadra"] == quadra_target)
    ].copy()

    if filtro.empty:
        raise ValueError(f"Quadra {quadra_target} não encontrada em {bairro_target}.")

    filtro["_busca_lote"] = filtro[COL_LOTE].apply(_normalizar_numero)
    mapa_db: dict[str, list[int]] = {}
    for _, row in filtro.iterrows():
        lote_str = str(row["_busca_lote"])
        id_lote  = int(row[COL_ID_LOTE])
        mapa_db.setdefault(lote_str, []).append(id_lote)
        # Handle composite lote codes like "5/6" or "5-6"
        if "/" in lote_str or "-" in lote_str:
            for part in re.split(r"[/\-]", lote_str):
                p = _normalizar_numero(part)
                mapa_db.setdefault(p, []).append(id_lote)

    ids_finais: list[int] = []
    for lote in dados_ia["lotes"]:
        norm = _normalizar_numero(lote)
        if norm in mapa_db:
            ids_finais.extend(mapa_db[norm])

    ids_unicos = list({int(x) for x in ids_finais})
    if not ids_unicos:
        raise ValueError(f"Lotes {dados_ia['lotes']} não localizados na base.")
    return ids_unicos


def _buscar_vertices(ids_lotes: list[int]) -> pd.DataFrame:
    """Fetch polygon vertices from Supabase for the given lote IDs."""
    if supabase is None:
        raise RuntimeError("Supabase client não inicializado. Verifique SUPABASE_URL e SUPABASE_KEY.")

    resp = (
        supabase.table("vertices")
        .select("id_lote, latitude, longitude")
        .in_("id_lote", ids_lotes)
        .order("id_lote")
        .order("vertice_idx")
        .execute()
    )
    if not resp.data:
        raise ValueError(f"Nenhum vértice encontrado para IDs: {ids_lotes}")
    return pd.DataFrame(resp.data)


def _construir_poligono_e_kmz(df_vertices: pd.DataFrame, dados_ia: dict) -> tuple[str, tuple[float, float]]:
    """
    Build a unified polygon from vertex data and save as KMZ.

    Returns:
        kmz_path:  Path to the saved .kmz file.
        centroide: (latitude, longitude) of the polygon centroid.
    """
    poligonos = []
    for _id_lote, group in df_vertices.groupby("id_lote", sort=False):
        coords = list(zip(group["longitude"], group["latitude"]))
        if len(coords) >= 3:
            poligonos.append(Polygon(coords))

    if not poligonos:
        raise ValueError("Nenhum polígono válido construído a partir dos vértices.")

    gdf   = gpd.GeoDataFrame({"geometry": poligonos}, crs="EPSG:4326")
    uniao = gdf.union_all() if hasattr(gdf, "union_all") else gdf.unary_union

    # ── Build KML ───────────────────────────────────────────────────────────
    kml       = simplekml.Kml()
    lotes_str = "_".join(map(str, dados_ia["lotes"]))
    nome_area = f"QD. {dados_ia['quadra']} - {dados_ia['bairro']} - lotes {lotes_str}"

    pol = kml.newpolygon(name=nome_area)

    if uniao.geom_type == "Polygon":
        pol.outerboundaryis = list(uniao.exterior.coords)
    else:
        # MultiPolygon — use largest piece
        maior = max(uniao.geoms, key=lambda g: g.area)
        pol.outerboundaryis = list(maior.exterior.coords)

    pol.style.polystyle.color = simplekml.Color.changealphaint(150, simplekml.Color.cyan)
    pol.style.linestyle.color = simplekml.Color.blue
    pol.style.linestyle.width = 2

    # ── Save KMZ ────────────────────────────────────────────────────────────
    safe_name = re.sub(r'[\\/*?":<>|]', "", nome_area)
    kmz_path  = KMZ_OUTPUT_DIR / f"{safe_name}.kmz"
    kml.savekmz(str(kmz_path))

    # ── Centroid ─────────────────────────────────────────────────────────────
    centroide = (uniao.centroid.y, uniao.centroid.x)   # (lat, lon)

    print(f"✅ KMZ salvo: {kmz_path}")
    print(f"   Centróide: {centroide[0]:.6f}, {centroide[1]:.6f}")
    return str(kmz_path), centroide


# ═════════════════════════════════════════════════════════════════════════════
#  Public API
# ═════════════════════════════════════════════════════════════════════════════

def gerar_kmz_para_terreno(texto_endereco: str) -> tuple[str, tuple[float, float]]:
    """
    Full pipeline: text address → KMZ file + centroid.

    Args:
        texto_endereco: Free-text address (bairro, quadra, lote(s)).

    Returns:
        (kmz_path, (lat, lon))

    Raises:
        RuntimeError / ValueError on failure at any step.
    """
    texto_limpo = _limpar_texto(texto_endereco)

    url_lotes = os.getenv("URL_BASE_LOTES")
    if not url_lotes:
        raise RuntimeError("URL_BASE_LOTES não configurada.")

    print("1. Baixando hierarquia de lotes...")
    df_base = pd.read_csv(url_lotes)
    df_base.columns = df_base.columns.str.strip().str.lower()
    bairros = df_base[COL_BAIRRO].dropna().astype(str).unique().tolist()

    print("2. IA extraindo dados do endereço...")
    dados_ia = _extrair_dados_com_ia(texto_limpo, bairros)
    print(f"   → {dados_ia}")

    print("3. Cruzando IDs de lotes...")
    ids = _buscar_ids_lotes(dados_ia, df_base)
    print(f"   → IDs encontrados: {ids}")

    print("4. Buscando vértices no Supabase...")
    df_vertices = _buscar_vertices(ids)

    print("5. Gerando KMZ...")
    kmz_path, centroide = _construir_poligono_e_kmz(df_vertices, dados_ia)

    return kmz_path, centroide


def validar_kmz(kmz_path: str) -> bool:
    """
    Basic KMZ integrity check.
    Verifies the file exists and is a valid ZIP (KMZ = ZIP container).
    """
    import zipfile
    path = Path(kmz_path)
    if not path.exists() or path.stat().st_size == 0:
        print(f"❌ KMZ inválido ou vazio: {kmz_path}")
        return False
    try:
        with zipfile.ZipFile(kmz_path, "r") as z:
            names = z.namelist()
            if not any(n.endswith(".kml") for n in names):
                print(f"❌ KMZ sem arquivo .kml interno: {names}")
                return False
    except zipfile.BadZipFile:
        print(f"❌ KMZ corrompido (não é ZIP válido): {kmz_path}")
        return False
    print(f"✅ KMZ válido: {kmz_path}")
    return True
