# Release PST

Prepara una nueva versión de PailsSentinelTrade en una rama y abre el PR a `main`.
**No crea tags ni publica nada**: al fusionar el PR a `main`, la GitHub Action
(`.github/workflows/build-release.yml`) crea el tag `vX.X.X` y compila la Release.

## Uso
`/release vX.X.X - [descripción de los hitos]`

Ejemplo: `/release v2.9.0 - Nuevo filtro macro y backtesting mejorado`

## Instrucciones

`$ARGUMENTS` tiene el formato `vX.X.X - descripción` o solo `vX.X.X`.

Ejecuta estos pasos en orden:

### 1. Parsear versión
Extrae el número sin la "v" (ej. `2.9.0`) y la descripción opcional.

### 2. Comprobar el estado del repo
- Si `git status` muestra cambios sin relación con la release, pregunta antes de incluirlos.
- Si la versión ya existe como tag (`git ls-remote --tags origin refs/tags/vX.X.X`), para y avisa.

### 3. Rama de release
- Si estamos en `main`, crea una rama `release/vX.X.X` desde `main` actualizado.
- Si estamos en una rama de feature que el usuario quiere publicar, úsala.

### 4. Actualizar archivos de versión
- **`version.txt`** (raíz): solo el número, sin "v", ej. `2.9.0`. Es el que lee la Action.
- **`PST_UTIL/version.txt`**: ídem.
- **`PST_API/main.py`**: `version="X.X.X"` (buscar `version=` en la clase FastAPI).

### 5. Commit
Formato estricto, **sin** `Co-Authored-By`:
```
git add version.txt PST_UTIL/version.txt PST_API/main.py
git commit -m "vX.X.X - [descripción]"
```
Si hay otros cambios que deban ir en la release, añádelos antes de este commit (`git add -A` solo si el usuario lo confirma).

### 6. Confirmación única
Antes de publicar, muestra al usuario la rama, el mensaje del commit y el título del PR, y pide confirmación.
Este es el único punto de confirmación: el merge a `main` lo hace el usuario en GitHub.

### 7. Push y PR
```
git push -u origin <rama>
gh pr create --base main --head <rama> --title "vX.X.X - [descripción]" --body "[hitos]

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

### 8. Confirmar
Muestra la URL del PR y recuerda: al fusionarlo a `main`, la Action crea el tag `vX.X.X` y compila la Release (~10 min).

## Respaldo manual
Si hace falta lanzar un build sin PR (p. ej. reconstruir una versión ya existente), usa
`workflow_dispatch` de la Action en GitHub o empuja el tag a mano: `git push origin vX.X.X`.
