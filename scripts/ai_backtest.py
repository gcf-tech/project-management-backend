"""Backtest del planificador IA contra los proyectos históricos de un tablero.

Criterio de evaluación comprometido: los proyectos ya existentes son la referencia
de cómo el equipo organiza su trabajo. Para cada tarjeta padre con subtareas:

  1. Se construye el contexto del tablero EXCLUYENDO ese proyecto y sus subtareas
     (leave-one-out), para que la IA no vea la respuesta.
  2. Se genera una propuesta solo con el título + descripción de la tarjeta padre.
  3. Se compara contra lo que el equipo hizo: número de subtareas, solapamiento de
     títulos y coincidencia de asignados.

Se corre en modo `full` (con historial) y `none` (sin historial), replicando el
diseño con/sin contexto del estudio empírico. NO escribe nada en la base de datos.

Uso (raíz del backend, venv activo, túnel y .env listos):

    python -m scripts.ai_backtest --board 1 --dry            # lista los proyectos candidatos, sin llamar a la IA
    python -m scripts.ai_backtest --board 1                  # corre full + none sobre todos
    python -m scripts.ai_backtest --board 1 --modes full     # solo con contexto
    python -m scripts.ai_backtest --board 1 --limit 5        # los 5 más recientes
    python -m scripts.ai_backtest --board 1 --out docs/ai-planning/backtest-tech

Salida: <out>.csv (una fila por proyecto y modo), <out>.json (propuestas y
detalle) y un resumen por modo en consola.

Métricas por proyecto:
  n_real, n_ai            subtareas reales / propuestas
  size_ratio              n_ai / n_real (1.0 = misma granularidad)
  title_coverage          % de subtareas reales con alguna propuesta equivalente (coseno de embeddings >= 0.55)
  title_similarity        promedio de la mejor similitud de cada subtarea real
  lexical_coverage        la misma cobertura con Jaccard de palabras (>= 0.25), solo como referencia
  assignee_pool_precision % de propuestas cuyo asignado participó realmente en el proyecto
  assignee_pair_accuracy  % de pares (real <-> propuesta emparejada) con el mismo asignado
  dominant_match          1 si la persona con más subtareas coincide (real vs IA)
  phase_warnings          nº de avisos de "subtarea de fase" de la validación
  unassigned_ai           nº de propuestas sin asignado
"""
import argparse
import asyncio
import csv
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.db.database import SessionLocal
from app.db.models import DeckBoard, DeckCard
from app.services.ai_context import build_board_context, _strip_html
from app.services import ai_planner
from app.services.ai_svc import AIError, embed
from app.core import config

SEM_MATCH = 0.55       # coseno de embeddings a partir del cual dos títulos son "la misma pieza"
STOP = set("""de la el los las un una unos unas y o a en del al para por con sin que se su sus
lo le como mas más es son ser está esta este esto estos estas the of to and in on for""".split())
MIN_DESC = 40          # descripción mínima del padre para que la prueba sea justa
JACCARD_MATCH = 0.25


def _tokens(s: str) -> set:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return {t for t in re.findall(r"[a-z0-9]{3,}", s) if t not in STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cos(a: List[float], b: List[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5; db = sum(y * y for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


async def _similarity_matrix(real: List[Dict], ai: List[Dict]) -> Tuple[List[List[float]], str]:
    """Matriz real×ia de similitud de títulos: coseno de embeddings; si la API
    falla, Jaccard de tokens. Devuelve (matriz, método)."""
    try:
        vecs = await embed([r["title"] for r in real] + [t["title"] for t in ai])
        rv, av = vecs[:len(real)], vecs[len(real):]
        return [[_cos(a, b) for b in av] for a in rv], "embeddings"
    except Exception as e:  # noqa: BLE001
        print(f"(embeddings no disponibles: {e}; se usa Jaccard)")
        return [[_jaccard(_tokens(r["title"]), _tokens(t["title"])) for t in ai] for r in real], "jaccard"


def _match(real: List[Dict], ai: List[Dict], sim: List[List[float]]) -> List[Tuple[int, int, float]]:
    """Emparejamiento voraz real<->ia por similitud de títulos (cada uno a lo sumo una vez)."""
    pairs = []
    for i, r in enumerate(real):
        for j, t in enumerate(ai):
            pairs.append((sim[i][j], i, j))
    pairs.sort(reverse=True)
    used_r, used_a, out = set(), set(), []
    for score, i, j in pairs:
        if i in used_r or j in used_a:
            continue
        used_r.add(i); used_a.add(j); out.append((i, j, score))
    return out


def candidates(db, board, min_subtasks: int):
    parent_ids = [r[0] for r in db.query(DeckCard.parent_card_id)
                  .filter(DeckCard.parent_card_id.isnot(None)).distinct().all()]
    parents = (db.query(DeckCard).filter(DeckCard.id.in_(parent_ids), DeckCard.board_id == board.id,
                                         DeckCard.archived.is_(False))
               .order_by(DeckCard.id.desc()).all()) if parent_ids else []
    out = []
    for p in parents:
        subs = db.query(DeckCard).filter(DeckCard.parent_card_id == p.id).order_by(DeckCard.created_at).all()
        real = [{"title": s.title, "assignees": sorted(a.user_id for a in s.assignees)} for s in subs]
        desc = _strip_html(p.description).strip()
        reason = None
        if len(real) < min_subtasks:
            reason = f"solo {len(real)} subtareas"
        elif len(desc) < MIN_DESC:
            reason = f"descripción de {len(desc)} caracteres (<{MIN_DESC})"
        out.append({"id": p.id, "title": p.title, "description": desc, "real": real, "skip": reason})
    return out


async def evaluate(real: List[Dict], proposal: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    ai = proposal.get("tasks") or []
    n_real, n_ai = len(real), len(ai)
    sim, method = await _similarity_matrix(real, ai) if real and ai else ([], "none")
    thr = SEM_MATCH if method == "embeddings" else JACCARD_MATCH
    pairs = _match(real, ai, sim) if sim else []
    best = {i: s for i, j, s in pairs}
    coverage = sum(1 for i in range(n_real) if best.get(i, 0) >= thr) / n_real if n_real else 0
    similarity = sum(best.get(i, 0) for i in range(n_real)) / n_real if n_real else 0
    # Jaccard léxico como referencia (para documentar por qué no sirve solo)
    lex_best = [max((_jaccard(_tokens(r["title"]), _tokens(t["title"])) for t in ai), default=0) for r in real]
    lex_coverage = sum(1 for v in lex_best if v >= JACCARD_MATCH) / n_real if n_real else 0

    real_pool = {u for r in real for u in r["assignees"]}
    ai_assigned = [t for t in ai if t.get("suggested_assignee_id") is not None]
    pool_prec = (sum(1 for t in ai_assigned if t["suggested_assignee_id"] in real_pool) / len(ai_assigned)
                 if ai_assigned and real_pool else None)
    pair_hits, pair_tot = 0, 0
    for i, j, s in pairs:
        if s < thr or not real[i]["assignees"]:
            continue
        pair_tot += 1
        if ai[j].get("suggested_assignee_id") in real[i]["assignees"]:
            pair_hits += 1
    pair_acc = pair_hits / pair_tot if pair_tot else None

    def dominant(items):
        c = Counter(items)
        return c.most_common(1)[0][0] if c else None
    dom_real = dominant([u for r in real for u in r["assignees"]])
    dom_ai = dominant([t["suggested_assignee_id"] for t in ai_assigned])
    return {
        "n_real": n_real, "n_ai": n_ai,
        "size_ratio": round(n_ai / n_real, 2) if n_real else None,
        "title_method": method,
        "title_coverage": round(coverage, 2),
        "title_similarity": round(similarity, 2),
        "lexical_coverage": round(lex_coverage, 2),
        "assignee_pool_precision": None if pool_prec is None else round(pool_prec, 2),
        "assignee_pair_accuracy": None if pair_acc is None else round(pair_acc, 2),
        "dominant_match": (None if dom_real is None or dom_ai is None else int(dom_real == dom_ai)),
        "phase_warnings": sum(1 for w in warnings if "fase del ciclo" in w),
        "evidence_warnings": sum(1 for w in warnings if "evidencia" in w or "cita" in w),
        "unassigned_ai": n_ai - len(ai_assigned),
        "pairs": [{"real": real[i]["title"], "ai": ai[j]["title"], "jaccard": round(s, 2),
                   "real_assignees": real[i]["assignees"], "ai_assignee": ai[j].get("suggested_assignee_id")}
                  for i, j, s in pairs],
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    return {
        "projects": len(rows),
        "avg_size_ratio": avg("size_ratio"),
        "avg_title_coverage": avg("title_coverage"),
        "avg_title_similarity": avg("title_similarity"),
        "avg_lexical_coverage": avg("lexical_coverage"),
        "avg_assignee_pool_precision": avg("assignee_pool_precision"),
        "avg_assignee_pair_accuracy": avg("assignee_pair_accuracy"),
        "dominant_match_rate": avg("dominant_match"),
        "total_phase_warnings": sum(r["phase_warnings"] for r in rows),
        "total_evidence_warnings": sum(r["evidence_warnings"] for r in rows),
        "avg_unassigned_ai": avg("unassigned_ai"),
        "total_tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", type=int, required=True)
    ap.add_argument("--modes", default="full,none", help="full, none o full,none")
    ap.add_argument("--limit", type=int, default=0, help="solo los N proyectos más recientes")
    ap.add_argument("--min-subtasks", type=int, default=2)
    ap.add_argument("--ids", help="ids de proyectos separados por coma (ignora --limit)")
    ap.add_argument("--out", default="backtest", help="prefijo de salida (.csv y .json)")
    ap.add_argument("--dry", action="store_true", help="solo listar candidatos")
    ap.add_argument("--sleep", type=float, default=1.0, help="segundos entre llamadas")
    args = ap.parse_args()
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in ("full", "none")]

    db = SessionLocal()
    try:
        board = db.query(DeckBoard).filter(DeckBoard.id == args.board).first()
        if not board:
            print("tablero no existe"); sys.exit(1)
        cands = candidates(db, board, args.min_subtasks)
        if args.ids:
            wanted = {int(x) for x in args.ids.split(",")}
            cands = [c for c in cands if c["id"] in wanted]
        usable = [c for c in cands if not c["skip"]]
        if args.limit:
            usable = usable[:args.limit]

        print(f"Tablero {board.id} {board.title}: {len(cands)} proyectos, {len(usable)} evaluables")
        for c in cands:
            flag = f"  OMITIDO ({c['skip']})" if c["skip"] else ""
            print(f"  #{c['id']:<5} {len(c['real']):>2} sub | {c['title'][:70]}{flag}")
        if args.dry:
            return
        if not config.AI_ENABLED:
            print("OPENAI_API_KEY no configurada"); sys.exit(1)

        rows, detail = [], []
        total = len(usable) * len(modes)
        k = 0
        for c in usable:
            text = f"{c['title']}\n\n{c['description']}"
            for mode in modes:
                k += 1
                ctx = build_board_context(db, board, include_history=(mode == "full"), exclude_project_id=c["id"])
                name_of = {m["id"]: m["name"] for m in ctx["members"]}
                print(f"[{k}/{total}] #{c['id']} {mode} … ", end="", flush=True)
                try:
                    gen = await ai_planner.generate_plan(ctx, text)
                except AIError as e:
                    print(f"ERROR {e}")
                    rows.append({"project_id": c["id"], "mode": mode, "error": str(e)})
                    continue
                ev = await evaluate(c["real"], gen["proposal"], gen["warnings"])
                row = {"project_id": c["id"], "project": c["title"][:60], "mode": mode, "model": gen["model"],
                       "prompt_tokens": gen["usage"]["prompt_tokens"], "completion_tokens": gen["usage"]["completion_tokens"],
                       **{k2: v for k2, v in ev.items() if k2 != "pairs"}}
                rows.append(row)
                detail.append({"project_id": c["id"], "mode": mode, "real": c["real"], "proposal": gen["proposal"],
                               "warnings": gen["warnings"], "pairs": ev["pairs"],
                               "member_names": {str(k3): v for k3, v in name_of.items()}})
                print(f"real {ev['n_real']} / ia {ev['n_ai']} | cobertura {ev['title_coverage']} | "
                      f"asignado pool {ev['assignee_pool_precision']} par {ev['assignee_pair_accuracy']} | "
                      f"fase {ev['phase_warnings']}")
                time.sleep(args.sleep)

        ok_rows = [r for r in rows if "error" not in r]
        fields = sorted({k4 for r in rows for k4 in r.keys()}, key=lambda x: (x not in ("project_id", "project", "mode"), x))
        with open(args.out + ".csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
        summary = {m: summarize([r for r in ok_rows if r["mode"] == m]) for m in modes}
        with open(args.out + ".json", "w", encoding="utf-8") as f:
            json.dump({"board_id": board.id, "model": config.AI_MODEL, "summary": summary,
                       "rows": rows, "detail": detail}, f, ensure_ascii=False, indent=2)

        print("\n== RESUMEN")
        for m in modes:
            print(f"-- modo {m}")
            for k5, v in summary[m].items():
                print(f"   {k5:<30} {v}")
        print(f"\nGuardado: {args.out}.csv y {args.out}.json")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
