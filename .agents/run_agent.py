#!/usr/bin/env python
"""
Runner de la flota de agentes PST.

Invoca un agente de Claude Code en modo desatendido, valida su salida contra
.agents/schemas/proposal.json e inserta las propuestas en la tabla proposals. Es lo que
cierra el circuito: sin esto los agentes producen informes que nadie lee.

Ver manual_docs/PLAN_AGENTES.md, secciones 3 y 6d.

USO
    python .agents/run_agent.py reconciliador
    python .agents/run_agent.py juez --model sonnet --effort low
    python .agents/run_agent.py reconciliador --dry-run     # no escribe en BBDD

POR QUE EL CLI Y NO UN SDK
    La cuenta es de suscripcion, sin API key. El SDK de Anthropic, el Tool Runner y
    Managed Agents exigen ANTHROPIC_API_KEY y facturan por token; el Agent SDK prohibe
    usar login de claude.ai en productos. La via valida -y la que la doc oficial senala
    para orquestar el agent loop desde otro lenguaje- es `claude -p` como subproceso.

CUOTA
    Cada ejecucion consume de la ventana de 5h y del tope semanal COMPARTIDOS con el
    trabajo interactivo del usuario. De ahi que este runner corra UN agente por invocacion
    y registre el consumo en agent_runs: el presupuesto se gobierna con datos, no a ojo.
    No lances varios en paralelo.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".agents"
SCHEMA = AGENTS_DIR / "schemas" / "proposal.json"
PROMPTS_DIR = AGENTS_DIR / "prompts"
OUT_DIR = AGENTS_DIR / "out"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Perfil por agente. El tiering no es por coste en euros sino por consumo de ventana: los
# agentes mecanicos (corren labs y resumen numeros) van en sonnet/low; opus/high se reserva
# para donde hace falta criterio de verdad.
PROFILES = {
    "reconciliador":   {"model": "sonnet", "effort": "low"},
    "juez":            {"model": "sonnet", "effort": "low"},
    "revisor":         {"model": "sonnet", "effort": "medium"},
    "cazador":         {"model": "opus",   "effort": "high"},
    "ingeniero-mql5":  {"model": "opus",   "effort": "high"},
}

# Tools permitidas por agente. Deliberadamente estrechas: un agente con mas superficie de
# la que necesita improvisa. mcp__pst__* es el servidor de .mcp.json.
ALLOWED_TOOLS = {
    "reconciliador":  "mcp__pst__*,Read,Glob,Grep",
    "juez":           "mcp__pst__*,Read,Glob,Grep",
    "revisor":        "mcp__pst__*,Read,Glob,Grep,Bash(git *)",
    "cazador":        "mcp__pst__*,Read,Glob,Grep,mcp__metatrader5-mcp__*",
    "ingeniero-mql5": "mcp__pst__*,Read,Write,Edit,Glob,Grep,mcp__metatrader5-mcp__*,Bash(*metaeditor64*)",
}


def build_command(agent: str, model: str, effort: str, prompt: str) -> list:
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--effort", effort,
        "--output-format", "json",
        "--json-schema", SCHEMA.read_text(encoding="utf-8"),
        "--allowedTools", ALLOWED_TOOLS.get(agent, "mcp__pst__*,Read"),
        # Nadie va a estar delante a las 3 de la manana: lo que pediria permiso se deniega
        # solo en vez de dejar la ejecucion colgada hasta el timeout.
        "--permission-prompts", "none",
        "--add-dir", str(REPO_ROOT),
    ]


def extract_payload(stdout: str):
    """
    De la envoltura de --output-format json saca (payload_del_agente, tokens).

    `claude -p --output-format json` devuelve un objeto con metadatos y el texto del
    agente en `result`. Con --json-schema ese texto es el JSON validado, pero puede venir
    ya parseado o como cadena segun version, asi que se cubren los dos casos.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return None, None

    usage = envelope.get("usage") or {}
    tokens = None
    if isinstance(usage, dict):
        tokens = sum(v for v in usage.values() if isinstance(v, int)) or None

    result = envelope.get("result", envelope)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None, tokens
    return result, tokens


def validate(payload) -> list:
    """Errores de forma, en lenguaje claro. Devuelve [] si la salida es usable."""
    errs = []
    if not isinstance(payload, dict):
        return ["La salida no es un objeto JSON"]
    for campo in ("agent", "summary", "proposals"):
        if campo not in payload:
            errs.append(f"Falta el campo obligatorio '{campo}'")
    props = payload.get("proposals")
    if props is not None and not isinstance(props, list):
        # Se corta aqui: iterar una cadena daria un error por caracter y enterraria la
        # causa real bajo ruido.
        errs.append("'proposals' tiene que ser un array")
        return errs
    for i, p in enumerate(props or []):
        if not isinstance(p, dict):
            errs.append(f"proposals[{i}] no es un objeto"); continue
        for campo in ("kind", "target", "rationale", "evidence"):
            if campo not in p:
                errs.append(f"proposals[{i}]: falta '{campo}'")
    return errs


async def persist(agent: str, model: str, effort: str, payload: dict,
                  tokens, duration: float, out_file: Path) -> dict:
    """Abre la ejecucion en agent_runs, cuelga las propuestas y la cierra."""
    from PST_Core.models.database import PSTDatabase
    from PST_Core.config import DB_PATH

    db = PSTDatabase(db_path=DB_PATH)
    await db.initialize()
    run_id = await db.start_agent_run(agent, model=model, effort=effort)

    ids = []
    for p in payload.get("proposals", []):
        ids.append(await db.add_proposal(
            agent_run_id=run_id,
            kind=p["kind"],
            target=p["target"],
            current_json=json.dumps(p.get("current"), ensure_ascii=False) if p.get("current") else None,
            proposed_json=json.dumps(p.get("proposed"), ensure_ascii=False) if p.get("proposed") else None,
            evidence_json=json.dumps(p.get("evidence"), ensure_ascii=False),
            judge_delta_eur=p.get("judge_delta_eur"),
            rationale=p["rationale"],
        ))

    await db.finish_agent_run(
        run_id, "ok", summary=payload.get("summary"), cost_tokens=tokens,
        duration_s=round(duration, 1),
        artifacts_json=json.dumps({"salida": str(out_file), "proposal_ids": ids}),
    )
    return {"run_id": run_id, "proposal_ids": ids}


async def record_failure(agent, model, effort, duration, error, out_file):
    """Una pasada fallida tambien se registra: si no, el consumo de cuota se pierde."""
    from PST_Core.models.database import PSTDatabase
    from PST_Core.config import DB_PATH
    db = PSTDatabase(db_path=DB_PATH)
    await db.initialize()
    run_id = await db.start_agent_run(agent, model=model, effort=effort)
    await db.finish_agent_run(run_id, "error", duration_s=round(duration, 1),
                              error=str(error)[:2000],
                              artifacts_json=json.dumps({"salida": str(out_file)}))
    return run_id


def main():
    ap = argparse.ArgumentParser(description="Lanza un agente de la flota PST")
    ap.add_argument("agent", choices=sorted(PROFILES))
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--dry-run", action="store_true", help="No escribe en BBDD; deja el JSON en .agents/out")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    profile = PROFILES[args.agent]
    model = args.model or profile["model"]
    effort = args.effort or profile["effort"]

    prompt_file = PROMPTS_DIR / f"{args.agent}.md"
    if not prompt_file.is_file():
        sys.exit(f"No existe el prompt {prompt_file}")
    if not SCHEMA.is_file():
        sys.exit(f"No existe el esquema {SCHEMA}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_file = OUT_DIR / f"{args.agent}_{stamp}.json"

    cmd = build_command(args.agent, model, effort, prompt_file.read_text(encoding="utf-8"))
    print(f"[{args.agent}] modelo={model} effort={effort} timeout={args.timeout}s")

    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=args.timeout)
    except subprocess.TimeoutExpired:
        duration = time.time() - started
        asyncio.run(record_failure(args.agent, model, effort, duration, "timeout", out_file))
        sys.exit(f"[{args.agent}] timeout a los {args.timeout}s (registrado en agent_runs)")
    except FileNotFoundError:
        sys.exit("No encuentro el ejecutable `claude` en el PATH.")

    duration = time.time() - started
    out_file.write_text(proc.stdout or "", encoding="utf-8")

    if proc.returncode != 0:
        asyncio.run(record_failure(args.agent, model, effort, duration,
                                   (proc.stderr or "")[:2000], out_file))
        sys.exit(f"[{args.agent}] claude salio con codigo {proc.returncode}. Salida en {out_file}")

    payload, tokens = extract_payload(proc.stdout or "")
    if payload is None:
        asyncio.run(record_failure(args.agent, model, effort, duration,
                                   "no se pudo parsear la salida", out_file))
        sys.exit(f"[{args.agent}] salida no parseable. Revisa {out_file}")

    errs = validate(payload)
    if errs:
        asyncio.run(record_failure(args.agent, model, effort, duration,
                                   "; ".join(errs), out_file))
        sys.exit(f"[{args.agent}] salida invalida:\n  - " + "\n  - ".join(errs))

    n = len(payload.get("proposals", []))
    print(f"[{args.agent}] {payload.get('summary','(sin resumen)')}")
    print(f"[{args.agent}] {n} propuesta(s) | {tokens or '?'} tokens | {duration:.0f}s")

    if args.dry_run:
        print(f"[{args.agent}] dry-run: no se escribio en BBDD. JSON en {out_file}")
        return

    res = asyncio.run(persist(args.agent, model, effort, payload, tokens, duration, out_file))
    print(f"[{args.agent}] agent_run #{res['run_id']} | propuestas {res['proposal_ids'] or 'ninguna'}")
    if n:
        print(f"[{args.agent}] Nada aplicado: decide en la bandeja de propuestas.")


if __name__ == "__main__":
    main()
