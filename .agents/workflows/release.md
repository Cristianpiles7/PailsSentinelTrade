---
description: Preparar una release de PST: versión, commit en rama y PR a main. El tag y el build los crea la GitHub Action al fusionar.
---

Este workflow es el mismo que `.claude/commands/release.md`. Ese archivo es la fuente de verdad: síguelo.

Resumen:
1. Subir `version.txt` (solo número, sin "v") y `PST_UTIL/version.txt` y `PST_API/main.py`.
2. Commit `vX.X.X - descripción`, sin `Co-Authored-By`.
3. Empujar la rama y abrir el PR a `main` (confirmación única antes de publicar).
4. El usuario fusiona el PR. La Action `build-release.yml` crea el tag `vX.X.X` y compila la Release.

No crear tags a mano salvo como respaldo.
