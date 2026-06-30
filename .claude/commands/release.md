# Release PST

Automatiza el commit, tag y push de una nueva versión de PailsSentinelTrade.

## Uso
`/release vX.X.X - [descripción de los hitos]`

Ejemplo: `/release v2.1.0 - Nuevo filtro macro y backtesting mejorado`

## Instrucciones

Cuando el usuario invoque este comando, el argumento `$ARGUMENTS` tendrá el formato `vX.X.X - descripción` o solo `vX.X.X`.

Ejecuta estos pasos en orden:

### 1. Parsear versión
Extrae el número de versión sin la "v" (ej. `2.1.0`) y la descripción opcional.

### 2. Actualizar archivos de versión

- **`version.txt`** (raíz): reemplazar el contenido con solo el número, ej. `2.1.0`
- **`PST_UTIL/version.txt`**: ídem
- **`PST_API/main.py` línea ~111**: cambiar `version="X.X.X"` con la nueva versión
- **`PST_Web/src/App.jsx` línea ~1238**: cambiar `PST-CORE: VX.X.X SMC` con la nueva versión en mayúsculas

### 3. Staging
```
git add version.txt PST_UTIL/version.txt PST_API/main.py PST_Web/src/App.jsx
git add -A
```

### 4. Commit
Formato estricto (sin Co-Authored-By en este commit):
```
git commit -m "vX.X.X - [descripción]"
```

### 5. Tag — borrar si ya existe
```
git tag -d vX.X.X
git push origin --delete vX.X.X
git tag vX.X.X
```
(los borrados pueden fallar si no existía, ignorar errores)

### 6. Push — pedir confirmación al usuario antes de ejecutar
```
git push origin <rama_actual>
git push origin vX.X.X
```

### 7. Confirmar
Mostrar al usuario el tag subido y recordarle que la GitHub Action de build se habrá disparado automáticamente.
