import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///./test_hoftalon.db"

import main


def _build_payload(objetivo: str) -> dict:
    return {
        "report_style": "hoftalon",
        "template": "",
        "data": {
            "cidade": "Sao Paulo",
            "unidade": "Unidade A",
            "periodo": "Q1 2025",
            "data_base": "2025-01-01",
            "responsavel_tecnico": "Joao Lima",
            "objetivo": objetivo,
            "escopo": "Escopo do relatorio.",
            "metodologia": "Metodologia aplicada.",
            "achados": ["Item 1", "Item 2", "Item 3"],
        },
        "tables": [
            {
                "key": "resultados_1",
                "csv": "col_a,col_b\n1,2",
                "delimiter": ",",
                "has_header": True,
            },
            {
                "key": "resultados_2",
                "csv": "col_a,col_b\n3,4",
                "delimiter": ",",
                "has_header": True,
            },
            {
                "key": "atividades",
                "csv": (
                    "atividade,responsavel,prazo_dias,prioridade,observacao\n"
                    "Inventario,Equipe,10,Alta,Ok"
                ),
                "delimiter": ",",
                "has_header": True,
            },
        ],
    }


class HoftalonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app)

    def test_hoftalon_ok(self) -> None:
        async def fake_llm(*_args, **_kwargs):
            table = (
                "<table>"
                "<thead><tr><th>col_a</th><th>col_b</th></tr></thead>"
                "<tbody><tr><td>1</td><td>2</td></tr></tbody>"
                "</table>"
            )
            meta = {
                "columns": ["col_a", "col_b"],
                "row_count": 1,
                "delimiter": ",",
                "has_header": True,
                "sampled": False,
                "truncated": False,
            }
            return table, meta

        payload = _build_payload("Objetivo do relatorio.")
        with patch(
            "main.generate_llm_html_from_csv", new=AsyncMock(side_effect=fake_llm)
        ):
            response = self.client.post("/api/render_with_tables", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        output_html = response.json().get("html", "")
        self.assertIn(main.HOFTALON_TITLE, output_html)
        self.assertIn("<h2>5. PLANO DE AÇÃO</h2>", output_html)

    def test_hoftalon_template_missing_section(self) -> None:
        payload = _build_payload("Objetivo valido.")
        payload["template"] = (
            '<section class="print-cover"><h1>RELATÓRIO TÉCNICO DE INVENTÁRIO</h1></section>'
            "<div class=\"page-break\"></div>"
            "<p>Cidade: {{ cidade }}</p>"
            "<p>Unidade: {{ unidade }}</p>"
            "<p>Período: {{ periodo }}</p>"
            "<p>Data-base: {{ data_base }}</p>"
            "<p>Responsável técnico: {{ responsavel_tecnico }}</p>"
            "<h2>1. OBJETIVO</h2><p>{{ objetivo }}</p>"
            "<h2>2. ESCOPO</h2><p>{{ escopo }}</p>"
            "<h2>4. RESULTADOS</h2>"
            "<h3>4.1 Resultados</h3>{{ tables_html['resultados_1'] }}"
            "<h3>4.2 Resultados</h3>{{ tables_html['resultados_2'] }}"
            "<h2>5. PLANO DE AÇÃO</h2>{{ tables_html['atividades'] }}"
            "<h2>6. ACHADOS / OBSERVAÇÕES</h2><ul><li>a</li><li>b</li><li>c</li></ul>"
        )
        response = self.client.post("/api/render_with_tables", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def test_hoftalon_block_system_terms(self) -> None:
        async def fake_llm(*_args, **_kwargs):
            table = (
                "<table>"
                "<thead><tr><th>col_a</th><th>col_b</th></tr></thead>"
                "<tbody><tr><td>1</td><td>2</td></tr></tbody>"
                "</table>"
            )
            meta = {
                "columns": ["col_a", "col_b"],
                "row_count": 1,
                "delimiter": ",",
                "has_header": True,
                "sampled": False,
                "truncated": False,
            }
            return table, meta

        payload = _build_payload("Objetivo citando FastAPI.")
        with patch(
            "main.generate_llm_html_from_csv", new=AsyncMock(side_effect=fake_llm)
        ):
            response = self.client.post("/api/render_with_tables", json=payload)
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
