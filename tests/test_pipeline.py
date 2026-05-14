"""
test_pipeline.py
────────────────
Automated tests for the diarioOficial-kmz pipeline.

Coverage:
  1. KMZ generation from 3 fictitious terrenos.
  2. Centroid calculation accuracy.
  3. KMZ integrity validation.
  4. Telegram sender (mocked — no real network calls).
  5. monitor_diario_kmz helpers (PDF extraction, dedup logic).

Run:
  pytest tests/test_pipeline.py -v
"""

import os
import json
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
from shapely.geometry import Polygon

# ─────────────────────────────────────────────────────────────────────────────
#  Fictitious terrenos used across all tests
# ─────────────────────────────────────────────────────────────────────────────

TERRENOS_FICTICIOS = [
    {
        # Terreno 1 — downtown-style small lot
        "name": "Terreno Centro",
        "bairro": "SETOR CENTRAL",
        "quadra": "5",
        "lotes": ["10", "11"],
        # Approximate Goiânia coords (fictitious)
        "vertices": [
            {"id_lote": 101, "latitude": -16.6800, "longitude": -49.2550},
            {"id_lote": 101, "latitude": -16.6800, "longitude": -49.2540},
            {"id_lote": 101, "latitude": -16.6810, "longitude": -49.2540},
            {"id_lote": 101, "latitude": -16.6810, "longitude": -49.2550},
            {"id_lote": 102, "latitude": -16.6810, "longitude": -49.2560},
            {"id_lote": 102, "latitude": -16.6810, "longitude": -49.2540},
            {"id_lote": 102, "latitude": -16.6820, "longitude": -49.2540},
            {"id_lote": 102, "latitude": -16.6820, "longitude": -49.2560},
        ],
        "centroide_esperado": (-16.6810, -49.2550),
        "tolerancia": 0.001,
    },
    {
        # Terreno 2 — larger suburban lot
        "name": "Terreno Jardins",
        "bairro": "JARDIM GOIAS",
        "quadra": "12",
        "lotes": ["3"],
        "vertices": [
            {"id_lote": 201, "latitude": -16.7100, "longitude": -49.2200},
            {"id_lote": 201, "latitude": -16.7100, "longitude": -49.2150},
            {"id_lote": 201, "latitude": -16.7130, "longitude": -49.2150},
            {"id_lote": 201, "latitude": -16.7130, "longitude": -49.2200},
        ],
        "centroide_esperado": (-16.7115, -49.2175),
        "tolerancia": 0.001,
    },
    {
        # Terreno 3 — corner lot, irregular shape
        "name": "Terreno Buena Vista",
        "bairro": "BUENA VISTA",
        "quadra": "7",
        "lotes": ["22"],
        "vertices": [
            {"id_lote": 301, "latitude": -16.6500, "longitude": -49.3100},
            {"id_lote": 301, "latitude": -16.6490, "longitude": -49.3080},
            {"id_lote": 301, "latitude": -16.6510, "longitude": -49.3070},
            {"id_lote": 301, "latitude": -16.6525, "longitude": -49.3090},
            {"id_lote": 301, "latitude": -16.6520, "longitude": -49.3110},
        ],
        "centroide_esperado": (-16.6509, -49.3090),
        "tolerancia": 0.002,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_polygon_and_kmz(terreno: dict, output_dir: str) -> tuple[str, tuple]:
    """
    Build polygon from fictitious vertex data and write a real KMZ.
    Replicates the core logic of kmz_generator._construir_poligono_e_kmz
    without requiring Supabase or AI.
    """
    import simplekml
    from shapely.ops import unary_union
    import geopandas as gpd

    df = pd.DataFrame(terreno["vertices"])
    poligonos = []
    for id_lote, grp in df.groupby("id_lote", sort=False):
        coords = list(zip(grp["longitude"], grp["latitude"]))
        if len(coords) >= 3:
            poligonos.append(Polygon(coords))

    gdf   = gpd.GeoDataFrame({"geometry": poligonos}, crs="EPSG:4326")
    uniao = gdf.union_all() if hasattr(gdf, "union_all") else gdf.unary_union

    kml      = simplekml.Kml()
    lotes_str = "_".join(terreno["lotes"])
    nome      = f"QD. {terreno['quadra']} - {terreno['bairro']} - lotes {lotes_str}"
    pol       = kml.newpolygon(name=nome)

    if uniao.geom_type == "Polygon":
        pol.outerboundaryis = list(uniao.exterior.coords)
    else:
        maior = max(uniao.geoms, key=lambda g: g.area)
        pol.outerboundaryis = list(maior.exterior.coords)

    pol.style.polystyle.color = simplekml.Color.changealphaint(150, simplekml.Color.cyan)

    import re
    safe = re.sub(r'[\\/*?":<>|]', "", nome)
    kmz_path = os.path.join(output_dir, f"{safe}.kmz")
    kml.savekmz(kmz_path)

    centroide = (uniao.centroid.y, uniao.centroid.x)
    return kmz_path, centroide


# ─────────────────────────────────────────────────────────────────────────────
#  Test Classes
# ─────────────────────────────────────────────────────────────────────────────

class TestKMZGeneration(unittest.TestCase):
    """Test KMZ file generation for 3 fictitious terrenos."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _run_for_terreno(self, terreno: dict):
        kmz_path, centroide = _build_polygon_and_kmz(terreno, self.tmp)
        return kmz_path, centroide

    def test_terreno_centro_kmz_criado(self):
        """Terreno 1: KMZ file must be created and non-empty."""
        t = TERRENOS_FICTICIOS[0]
        kmz_path, _ = self._run_for_terreno(t)
        self.assertTrue(os.path.exists(kmz_path), f"KMZ não criado: {kmz_path}")
        self.assertGreater(os.path.getsize(kmz_path), 0, "KMZ está vazio")
        print(f"  ✅ {t['name']}: KMZ em {kmz_path}")

    def test_terreno_jardins_kmz_criado(self):
        """Terreno 2: KMZ file must be created and non-empty."""
        t = TERRENOS_FICTICIOS[1]
        kmz_path, _ = self._run_for_terreno(t)
        self.assertTrue(os.path.exists(kmz_path))
        self.assertGreater(os.path.getsize(kmz_path), 0)
        print(f"  ✅ {t['name']}: KMZ criado")

    def test_terreno_buena_vista_kmz_criado(self):
        """Terreno 3: KMZ file must be created and non-empty."""
        t = TERRENOS_FICTICIOS[2]
        kmz_path, _ = self._run_for_terreno(t)
        self.assertTrue(os.path.exists(kmz_path))
        self.assertGreater(os.path.getsize(kmz_path), 0)
        print(f"  ✅ {t['name']}: KMZ criado")


class TestKMZIntegrity(unittest.TestCase):
    """Test KMZ ZIP integrity and internal KML presence."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _assert_valid_kmz(self, terreno: dict):
        kmz_path, _ = _build_polygon_and_kmz(terreno, self.tmp)
        self.assertTrue(zipfile.is_zipfile(kmz_path), f"KMZ não é ZIP válido: {kmz_path}")
        with zipfile.ZipFile(kmz_path) as z:
            names = z.namelist()
            has_kml = any(n.endswith(".kml") for n in names)
            self.assertTrue(has_kml, f"KMZ sem .kml interno. Conteúdo: {names}")
        return kmz_path

    def test_kmz_e_zip_valido_centro(self):
        self._assert_valid_kmz(TERRENOS_FICTICIOS[0])

    def test_kmz_e_zip_valido_jardins(self):
        self._assert_valid_kmz(TERRENOS_FICTICIOS[1])

    def test_kmz_e_zip_valido_buena_vista(self):
        self._assert_valid_kmz(TERRENOS_FICTICIOS[2])

    def test_kml_tem_polygon(self):
        """KML inside KMZ must contain a Polygon element."""
        kmz_path = self._assert_valid_kmz(TERRENOS_FICTICIOS[0])
        with zipfile.ZipFile(kmz_path) as z:
            kml_name = next(n for n in z.namelist() if n.endswith(".kml"))
            kml_content = z.read(kml_name).decode("utf-8")
        self.assertIn("Polygon", kml_content, "KML não contém elemento Polygon")


class TestCentroidCalculation(unittest.TestCase):
    """Test centroid accuracy for all 3 fictitious terrenos."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _check_centroide(self, terreno: dict):
        _, centroide = _build_polygon_and_kmz(terreno, self.tmp)
        lat_esperada, lon_esperado = terreno["centroide_esperado"]
        tolerancia = terreno["tolerancia"]

        lat_diff = abs(centroide[0] - lat_esperada)
        lon_diff = abs(centroide[1] - lon_esperado)

        self.assertLess(
            lat_diff, tolerancia,
            f"Latitude centróide fora da tolerância: {centroide[0]:.6f} vs {lat_esperada:.6f}"
        )
        self.assertLess(
            lon_diff, tolerancia,
            f"Longitude centróide fora da tolerância: {centroide[1]:.6f} vs {lon_esperado:.6f}"
        )
        print(
            f"  ✅ {terreno['name']}: centróide={centroide[0]:.6f},{centroide[1]:.6f} "
            f"(Δlat={lat_diff:.6f}, Δlon={lon_diff:.6f})"
        )

    def test_centroide_terreno_centro(self):
        self._check_centroide(TERRENOS_FICTICIOS[0])

    def test_centroide_terreno_jardins(self):
        self._check_centroide(TERRENOS_FICTICIOS[1])

    def test_centroide_terreno_buena_vista(self):
        self._check_centroide(TERRENOS_FICTICIOS[2])


class TestTelegramSender(unittest.TestCase):
    """Test Telegram sender with mocked HTTP calls (no real network)."""

    @patch("src.telegram_sender.requests.post")
    def test_send_message_ok(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        os.environ["TELEGRAM_TOKEN"]   = "test_token"
        os.environ["TELEGRAM_CHAT_ID"] = "12345"

        # Re-import to pick up env vars
        import importlib
        import src.telegram_sender as ts
        importlib.reload(ts)

        ok = ts.send_message("Teste de mensagem")
        self.assertTrue(ok)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("sendMessage", call_kwargs[0][0])
        print("  ✅ send_message: chamada HTTP correta")

    @patch("src.telegram_sender.requests.post")
    def test_send_document_ok(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        os.environ["TELEGRAM_TOKEN"]   = "test_token"
        os.environ["TELEGRAM_CHAT_ID"] = "12345"

        import importlib
        import src.telegram_sender as ts
        importlib.reload(ts)

        # Create a real temp file to send
        with tempfile.NamedTemporaryFile(suffix=".kmz", delete=False) as f:
            f.write(b"PK\x03\x04fake_kmz_content")
            tmp_kmz = f.name

        try:
            ok = ts.send_document(tmp_kmz, caption="Teste KMZ")
            self.assertTrue(ok)
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            self.assertIn("sendDocument", call_kwargs[0][0])
            print("  ✅ send_document: chamada HTTP correta")
        finally:
            os.unlink(tmp_kmz)

    @patch("src.telegram_sender.requests.post")
    def test_send_terreno_notification_com_centroide(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        os.environ["TELEGRAM_TOKEN"]   = "test_token"
        os.environ["TELEGRAM_CHAT_ID"] = "12345"

        import importlib
        import src.telegram_sender as ts
        importlib.reload(ts)

        with tempfile.NamedTemporaryFile(suffix=".kmz", delete=False) as f:
            f.write(b"PK\x03\x04fake")
            tmp_kmz = f.name

        terreno = {
            "interessado": "João da Silva",
            "local": "Setor Central, Quadra 5, Lotes 10 e 11",
            "decisao": "Aprovada a certidão de remembramento.",
            "link_pdf": "https://example.com/diario.pdf",
        }
        centroide = (-16.6810, -49.2550)

        try:
            ok = ts.send_terreno_notification(terreno, kmz_path=tmp_kmz, centroide=centroide)
            self.assertTrue(ok)
            # 2 calls: one sendMessage + one sendDocument
            self.assertEqual(mock_post.call_count, 2)
            # Verify centroid in message
            msg_call = mock_post.call_args_list[0]
            msg_body = msg_call[1].get("json", {}).get("text", "")
            self.assertIn("-16.681000", msg_body)
            self.assertIn("-49.255000", msg_body)
            print(f"  ✅ send_terreno_notification: centróide na mensagem, {mock_post.call_count} chamadas HTTP")
        finally:
            os.unlink(tmp_kmz)

    def test_send_message_sem_token(self):
        """Should return False gracefully when token is missing."""
        os.environ.pop("TELEGRAM_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)

        import importlib
        import src.telegram_sender as ts
        importlib.reload(ts)

        ok = ts.send_message("Teste sem token")
        self.assertFalse(ok)
        print("  ✅ send_message sem token: retorna False graciosamente")


class TestDiarioMonitorHelpers(unittest.TestCase):
    """Test helper functions in monitor_diario_kmz (no network, no AI)."""

    def test_extrair_texto_pdf_bytes_invalidos(self):
        """_extrair_texto_pdf should return empty string for invalid bytes."""
        from src.monitor_diario_kmz import _extrair_texto_pdf
        resultado = _extrair_texto_pdf(b"not a pdf")
        self.assertIsInstance(resultado, str)
        # Should not raise, just return empty or partial
        print(f"  ✅ _extrair_texto_pdf: bytes inválidos tratados sem crash")

    def test_salvar_e_ler_ultimo_diario(self):
        """_salvar_ultimo_diario / _ler_ultimo_diario should persist correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                from src.monitor_diario_kmz import _salvar_ultimo_diario, _ler_ultimo_diario
                _salvar_ultimo_diario("https://example.com/diario-2026.pdf")
                lido = _ler_ultimo_diario()
                self.assertEqual(lido, "https://example.com/diario-2026.pdf")
                print(f"  ✅ ultimo_diario: salvo e lido corretamente")
            finally:
                os.chdir(original_cwd)

    def test_ler_ultimo_diario_arquivo_inexistente(self):
        """_ler_ultimo_diario should return empty string if file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                from src.monitor_diario_kmz import _ler_ultimo_diario
                resultado = _ler_ultimo_diario()
                self.assertEqual(resultado, "")
                print("  ✅ _ler_ultimo_diario: arquivo inexistente retorna ''")
            finally:
                os.chdir(original_cwd)


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  diarioOficial-kmz — Test Suite")
    print("=" * 60)
    unittest.main(verbosity=2)
