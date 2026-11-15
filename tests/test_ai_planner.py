"""Pruebas del planificador IA sin red ni BD: validación programática,
conteo de ediciones y extracción de JSON."""
import pytest

from app.services.ai_planner import validate_and_normalize, count_manual_edits
from app.services.ai_svc import extract_json, AIError

CTX = {
    "members": [{"id": 1, "name": "Ana"}, {"id": 2, "name": "Luis"}],
    "history_projects": [{"id": 10}],
}


def test_normaliza_y_corrige_campos():
    data = {
        "project": {"title": "  Portal clientes ", "description": "x"},
        "tasks": [
            {"id": "T1", "title": "Diseño", "suggested_assignee_id": 1, "estimated_hours": "8",
             "confidence": "ALTA", "reference_project_id": 10, "depends_on": []},
            {"id": "T1", "title": "Backend", "suggested_assignee_id": 99, "estimated_hours": -3,
             "confidence": "quizás", "reference_project_id": 77, "depends_on": ["T1", "T9"]},
            {"id": "T3", "title": "", "suggested_assignee_id": 2},
        ],
        "assumptions": ["a"], "risks": [],
    }
    out, warnings = validate_and_normalize(data, CTX)
    assert out["project"]["title"] == "Portal clientes"
    assert [t["id"] for t in out["tasks"]] == ["T1", "T2"]      # duplicado renombrado, vacía descartada
    t2 = out["tasks"][1]
    assert t2["suggested_assignee_id"] is None                  # 99 no existe
    assert t2["estimated_hours"] is None                        # negativo
    assert t2["confidence"] == "baja"
    assert t2["reference_project_id"] is None                   # 77 no está en historial
    assert t2["depends_on"] == ["T1"]                           # T9 eliminada
    assert any("duplicado" in w for w in warnings)
    assert any("99" in w for w in warnings)


def test_cuenta_ediciones_por_campo():
    ai = {"project": {"title": "A", "description": ""},
          "tasks": [{"id": "T1", "title": "x", "suggested_assignee_id": 1, "estimated_hours": 4},
                    {"id": "T2", "title": "y", "suggested_assignee_id": 2, "estimated_hours": 2}]}
    final = {"project": {"title": "A", "description": "nueva"},
             "tasks": [{"id": "T1", "title": "x!", "suggested_assignee_id": 2, "estimated_hours": 4},
                       {"id": "T3", "title": "z", "suggested_assignee_id": 1, "estimated_hours": 1}]}
    c = count_manual_edits(ai, final)
    assert c["project"] == 1
    assert c["task_title"] == 1
    assert c["assignee"] == 1
    assert c["tasks_added"] == 1
    assert c["tasks_removed"] == 1
    assert c["total"] == 5


def test_sin_ediciones_es_cero():
    ai = {"project": {"title": "A", "description": ""}, "tasks": [{"id": "T1", "title": "x"}]}
    assert count_manual_edits(ai, ai)["total"] == 0


def test_extract_json_tolera_fences_y_texto():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('Claro, aquí va: {"a": {"b": "}"}} fin')["a"]["b"] == "}"
    with pytest.raises(AIError):
        extract_json("sin json")
