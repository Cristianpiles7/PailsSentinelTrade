---
description: Análisis de riesgo en tiempo real de las posiciones abiertas y el margen
---

Este workflow monitoriza tu exposición actual en el mercado para evitar sobre-apalancamiento.

### Pasos:

1. **Lectura de MetaTrader 5**:
   - Obtener las posiciones abiertas actualmente (`mt5.positions_get`).
   - Consultar la equidad y el margen libre actual de la cuenta.

2. **Cálculo de Exposición**:
   - Calcular el `% de riesgo flotante` respecto al balance.
   - Identificar símbolos con mayor peso en la equidad negativa.

3. **Alertas Sentinel**:
   - Notificar si el Drawdown del día se acerca al límite de seguridad (FTMO Safe).
   - Listar cada ticket abierto con su beneficio/pérdida actual.

### Uso Sugerido:
Escribe `/risk` cuando tengas muchas operaciones abiertas y necesites ver tu "temperatura" de riesgo de un solo vistazo.
