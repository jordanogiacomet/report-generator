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
            table = "| col_a | col_b |\n| --- | --- |\n| 1 | 2 |\n"
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
            "main.generate_llm_markdown_from_csv", new=AsyncMock(side_effect=fake_llm)
        ):
            response = self.client.post("/api/render_with_tables", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        markdown = response.json().get("markdown", "")
        self.assertIn(main.HOFTALON_TITLE, markdown)
        self.assertIn("## 5. PLANO DE AÇÃO", markdown)

    def test_hoftalon_template_missing_section(self) -> None:
        payload = _build_payload("Objetivo valido.")
        payload["template"] = (
            "# RELATÓRIO TÉCNICO DE INVENTÁRIO\n"
            "Cidade: {{ cidade }}\n"
            "Unidade: {{ unidade }}\n"
            "Período: {{ periodo }}\n"
            "Data-base: {{ data_base }}\n"
            "Responsável técnico: {{ responsavel_tecnico }}\n"
            "## 1. OBJETIVO\n{{ objetivo }}\n"
            "## 2. ESCOPO\n{{ escopo }}\n"
            "## 4. RESULTADOS\n"
            "### 4.1 Resultados\n{{ tables_md['resultados_1'] }}\n"
            "### 4.2 Resultados\n{{ tables_md['resultados_2'] }}\n"
            "## 5. PLANO DE AÇÃO\n{{ tables_md['atividades'] }}\n"
            "## 6. ACHADOS / OBSERVAÇÕES\n- a\n- b\n- c\n"
        )
        response = self.client.post("/api/render_with_tables", json=payload)
        self.assertEqual(response.status_code, 400, response.text)

    def test_hoftalon_block_system_terms(self) -> None:
        async def fake_llm(*_args, **_kwargs):
            table = "| col_a | col_b |\n| --- | --- |\n| 1 | 2 |\n"
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
            "main.generate_llm_markdown_from_csv", new=AsyncMock(side_effect=fake_llm)
        ):
            response = self.client.post("/api/render_with_tables", json=payload)
        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
