---
description: Obtener un reporte detallado del P&L de hoy desglosado por símbolo
---

Este workflow consulta la base de datos para darte una visión clara de qué activos han sido los más rentables en la sesión actual.

### Pasos:

1. **Consulta de Datos**:
   - Acceder a la tabla `trades` de la base de datos de producción.
   - Filtrar por operaciones cerradas hoy (`time_out >= today`).

2. **Procesamiento**:
   - Agrupar resultados por `symbol`.
   - Calcular:
     - `Profit Total` por símbolo.
     - `Número de trades` realizados.
     - `Win Rate %` por símbolo.

3. **Presentación**:
   - Mostrar una tabla resumen en el chat.
   - Resaltar en **verde** los activos ganadores y en **rojo** los que necesitan revisión.

### Uso Sugerido:
Escribe `/stats` para recibir una auditoría rápida de tu rendimiento diario sin tener que navegar por el Dashboard.
