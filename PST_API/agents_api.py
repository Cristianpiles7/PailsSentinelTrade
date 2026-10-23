"""
API de la flota de agentes: bandeja de propuestas y carrete de ejecuciones.

Es lo que cierra el circuito del plan (manual_docs/PLAN_AGENTES.md seccion 8). Sin una
bandeja, las propuestas de los agentes se quedan en filas de SQLite que nadie mira, y el
diseno entero -agentes que proponen, usuario que decide- no llega a existir en la practica.

IMPORTANTE SOBRE EL ORDEN DE REGISTRO: este router tiene que incluirse ANTES del catch-all
`@app.get("/{full_path:path}")` de main.py, que devuelve 404 para todo lo que empiece por
"api/". Starlette casa rutas por orden de registro.

QUIEN DECIDE: `decided_by` se fija SIEMPRE en el servidor, nunca viene del cliente. Esta
tabla es el registro de auditoria de lo que la IA ha tocado y quien lo aprobo; aceptar ese
campo del navegador lo convertiria en un campo de texto libre sin valor probatorio.
"""
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from PST_Core.config import DB_PATH
from PST_Core.models.database import PSTDatabase

logger = logging.getLogger("PST-AgentsAPI")

router = APIRouter(tags=["Agentes"])
_db = PSTDatabase(db_path=DB_PATH)

# Estados que la bandeja puede pedir. El servidor valida contra la maquina de estados de
# PSTDatabase, asi que aqui solo se filtra lo que es un estado reconocible.
_ESTADOS = {"propuesta", "shadow", "aprobada", "viva", "rechazada", "revertida"}


class DecisionRequest(BaseModel):
    """Lo UNICO que el cliente puede decir es a que estado quiere mover la propuesta."""
    state: str


@router.get("/api/agents/proposals")
async def list_proposals(state: str = None, limit: int = 50):
    """Propuestas para la bandeja, mas recientes primero."""
    if state and state not in _ESTADOS:
        raise HTTPException(status_code=400, detail=f"Estado desconocido: {state}")
    try:
        return await _db.list_proposals(state=state, limit=min(limit, 200))
    except Exception as e:
        # La tabla puede no existir si la BBDD no se ha inicializado todavia: se responde
        # vacio en vez de 500, para que la pestana pinte "sin propuestas" y no se rompa.
        logger.warning("No se pudieron leer las propuestas: %s", e)
        return []


@router.get("/api/agents/runs")
async def list_runs(limit: int = 20):
    """Ultimas ejecuciones, con su consumo. Es el carrete y el dato de presupuesto."""
    try:
        return await _db.list_agent_runs(limit=min(limit, 100))
    except Exception as e:
        logger.warning("No se pudieron leer las ejecuciones: %s", e)
        return []


@router.post("/api/agents/proposals/{proposal_id}/decide")
async def decide(proposal_id: int, req: DecisionRequest):
    """
    Mueve una propuesta de estado. La transicion la valida la maquina de estados de
    PSTDatabase, que rechaza saltos no previstos (p.ej. 'propuesta' directo a 'viva',
    saltandose la aprobacion) y resuelve las carreras entre dos pestanas.
    """
    if req.state not in _ESTADOS:
        raise HTTPException(status_code=400, detail=f"Estado desconocido: {req.state}")

    ok = await _db.decide_proposal(proposal_id, req.state, decided_by="usuario")
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Transición no permitida, o la propuesta cambió de estado mientras decidías.",
        )
    await asyncio.to_thread(_quitar_botones_rotacion, proposal_id)
    return {"ok": True, "proposal_id": proposal_id, "state": req.state}


def _quitar_botones_rotacion(proposal_id):
    """Si era una propuesta de la rotacion, quita los botones de su aviso de Telegram (decidida desde la app)."""
    con = sqlite3.connect(DB_PATH, timeout=15)
    try:
        fila = con.execute("SELECT kind, proposed_json FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    finally:
        con.close()
    if not fila or not str(fila[0]).startswith("rotacion_"):
        return
    llm = str(Path(__file__).resolve().parents[1] / "PST_LLM")
    if llm not in sys.path:
        sys.path.insert(0, llm)
    import rotacion       # noqa: E402  (PST_LLM/rotacion.py)
    rotacion._quitar_botones(fila[1])


@router.get("/api/agents/summary")
async def summary():
    """Contadores para la cabecera de la pestaña."""
    try:
        props = await _db.list_proposals(limit=200)
        runs = await _db.list_agent_runs(limit=50)
    except Exception:
        return {"pendientes": 0, "total": 0, "ultima_ejecucion": None, "tokens_ultimas": 0}

    por_estado = {}
    for p in props:
        por_estado[p["state"]] = por_estado.get(p["state"], 0) + 1

    return {
        "pendientes": por_estado.get("propuesta", 0),
        "por_estado": por_estado,
        "total": len(props),
        "ultima_ejecucion": runs[0] if runs else None,
        "tokens_ultimas": sum(r.get("cost_tokens") or 0 for r in runs),
    }
