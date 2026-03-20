---
description: Automatizar el proceso de commit, tag y lanzamiento de la GitHub Action (vX.X.X)
---

Este workflow asegura que los lanzamientos de PST sigan un orden estricto y profesional en GitHub.

### Pasos del Proceso:

1. **Preparación de Versión**:
   - Actualizar el archivo `version.txt` con el nuevo número (ej. `1.8.4`).
   - Asegurar que `PST_API/main.py` y `PST_Web/src/App.jsx` reflejen la nueva versión.

2. **Consolidación de Cambios**:
   - Ejecutar `git add .` para incluir todas las mejoras y correcciones.

3. **Commit Estandarizado**:
   - Realizar el commit siguiendo el formato histórico esperado por el usuario: 
   - `git commit -m "vX.X.X - [Breve descripción de los hitos principales]"`

4. **Tagging y Disparo de Action**:
   - Crear el tag local: `git tag vX.X.X`
   - Si el tag ya existía, se debe borrar primero: `git tag -d vX.X.X` y `git push origin --delete vX.X.X`.

5. **Subida Final**:
   // turbo
   - Ejecutar el push de la rama y del tag:
   - `git push origin [rama_actual]`
   - `git push origin vX.X.X`

### Uso Sugerido:
Cuando el usuario diga `/release vX.X.X`, el Agent deberá seguir estos pasos rigurosamente para garantizar que el `PST_Launcher` se genere correctamente y el historial de GitHub quede impecable.
