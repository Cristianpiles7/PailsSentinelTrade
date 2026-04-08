---
description: Extraer autopsias de pérdidas y analizar posibles mejoras para las estrategias actuales
---

Cuando el usuario escriba `/audit`, debes ejecutar este procedimiento de manera autónoma para proporcionarle un análisis constructivo:

1. **Ejecutar Extractor de Autopsias**:
   // turbo
   Ejecuta el script de exportación ubicado en `PST_Core/tools/export_loss_context.py` usando el comando:
   `python PST_Core/tools/export_loss_context.py --today`
   *(Si el usuario te ha especificado una estrategia, añade `--strategy "nombre_estrategia"`).*

2. **Leer la Evidencia**:
   Lee el contenido del archivo resultante `.agents/data/recent_losses.json` usando `view_file`.

3. **Auditoría e Identificación de Patrones**:
   - Analiza los OHLC del momento de entrada que se muestran en el archivo JSON para entender el contexto de mercado en el que el bot decidió entrar (ej: ¿Había demasiada volatilidad? ¿Se compró en un techo obvio contra la EMA? ¿Demasiada distancia del precio de apertura?).
   - Entiende en qué puntos el código falló y tomó una decisión prematura o errónea.
   
4. **Propuesta Accionable**:
   Proporciona un reporte en markdown al usuario indicando los hallazgos principales y sugiere 2-3 **modificaciones concretas al código de Python** de la estrategia (ej `pst_scalper_active.py` o `pst_ema_flow.py`) para prevenir que se repita este fallo.

5. **Ofrecer Implementación**:
   Termina preguntando al usuario si desea que apliques esos ajustes inmediatamente en el código usando tus tools de edición en vivo.
