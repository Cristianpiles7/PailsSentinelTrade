# Release PST (publicación desde Copailot)

Copailot la lanza desde el botón **Publicar** del ticket, dentro del worktree de la rama del ticket. Sube la versión
y publica la rama con commit y push.

**No abre PR, no crea tags, no hace merge y no toca `main`.** Copailot da el enlace para abrir la PR y la fusionas
tú. Al fusionar a `main`, la GitHub Action (`.github/workflows/build-release.yml`) crea el tag `vX.X.X` y compila la Release.

## Entradas
- `.copailot-publicacion/peticion.md`: la versión a publicar (`Publica la versión X.Y.Z de la rama ...`). Si ya
  preguntaste antes, trae la respuesta del usuario; en ese caso sigues la misma sesión.
- Trabaja solo en el worktree en el que estás.

## Pasos
1. Lee `.copailot-publicacion/peticion.md` y anota la versión X.Y.Z y la rama.
2. Comprueba `git status --porcelain --untracked-files=no`. Si hay cambios sin commitear que no son de la versión,
   no los añadas: haz una pregunta (ver «Preguntas»).
3. Pon la versión X.Y.Z en estos tres ficheros:
   - `version.txt`: solo el número, sin «v» (ej. `2.8.3`). Es el que lee la Action.
   - `PST_UTIL/version.txt`: lo mismo.
   - `PST_API/main.py`: la línea `version="X.Y.Z"` de la clase FastAPI.
4. `git add version.txt PST_UTIL/version.txt PST_API/main.py`
5. Commit con el formato del proyecto, **sin** `Co-Authored-By`:
   `git commit -m "vX.X.X - <resumen>"`. El resumen son los hitos de la rama, en una línea. Míralos con
   `git log --oneline origin/main..HEAD`.
6. Sube la rama actual: `git push -u origin <rama>`. La rama sale de `git rev-parse --abbrev-ref HEAD`.
   **Nunca** `--force`.
7. Escribe `.copailot-publicacion/respuesta.md` así:
   ```
   ---
   estado: ok
   ---
   Publicada vX.X.X en <rama>.
   ```

## Preguntas
Si necesitas una decisión del usuario, no hagas nada más. Escribe `.copailot-publicacion/respuesta.md` así:
```
---
estado: pregunta
---
<tu pregunta, en una o dos frases>
```
Copailot la muestra en la app. Cuando el usuario responde, se reanuda esta misma sesión y la respuesta está en
`.copailot-publicacion/peticion.md`.

Si algo falla, escribe `estado: error` con el motivo en el cuerpo, y no subas nada más.

## Reglas
- Solo `git add` de los tres ficheros de versión. Nada de `git add -A` ni `git add .`.
- No hagas PR (`gh pr create`), tags, merge, rebase ni push a `main`.
- No toques Jira ni la configuración de seguridad (`.claude/settings*.json`, hooks).
- No escribas secretos en ficheros, commits ni respuestas.
