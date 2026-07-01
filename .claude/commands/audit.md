# Auditoría de Pérdidas

Extrae autopsias de pérdidas recientes y analiza mejoras concretas para las estrategias activas.

## Uso
`/audit` — audita todas las estrategias del día
`/audit [nombre_estrategia]` — filtra por estrategia específica (ej: `/audit PST-Scalper-Active`)

## Instrucciones

Cuando el usuario invoque este comando, ejecuta estos pasos de manera autónoma:

### 1. Ejecutar el exportador de autopsias

Corre el script con el tool Bash:
```
python PST_Core/tools/export_loss_context.py --today
```
Si `$ARGUMENTS` contiene un nombre de estrategia, añade `--strategy "$ARGUMENTS"`.

Si el script falla o no existe el archivo resultante, informa al usuario y detente aquí.

### 2. Leer la evidencia

Lee el archivo `.agents/data/recent_losses.json` con el tool Read y examina su contenido completo.

### 3. Auditoría y análisis de patrones

Para cada pérdida en el JSON, analiza:
- **Contexto OHLC de entrada**: ¿Se compró en un techo obvio? ¿Demasiada distancia del precio de apertura? ¿Vela de entrada con mucho rango (alta volatilidad)?
- **Relación con EMAs**: ¿Entrada contra la EMA21 o EMA50? ¿Cruce reciente sin confirmación?
- **Timing**: ¿Entrada en apertura de sesión (spread alto)? ¿Fuera de horario óptimo?
- **Decisión prematura**: ¿Qué condición del código permitió esta entrada?

Busca el patrón común entre las pérdidas — eso es el fallo real.

### 4. Reporte accionable

Genera un reporte en markdown con:

```markdown
## Autopsia de Pérdidas — [fecha]

### Hallazgos principales
[2-3 observaciones sobre el patrón común detectado]

### Propuestas de mejora

#### 1. [Nombre del fix] — `[archivo.py]`
**Problema:** ...
**Solución propuesta:**
```python
# código antes
# código después
```

#### 2. [Nombre del fix] — `[archivo.py]`
...

#### 3. [Nombre del fix] — `[archivo.py]`
...
```

Limita las propuestas a 2-3 cambios concretos y de alto impacto en los archivos de estrategia relevantes (ej: `pst_scalper_active.py`, `pst_ema_flow.py`).

### 5. Ofrecer implementación

Termina preguntando al usuario:
> ¿Aplico estos ajustes directamente en el código ahora?
