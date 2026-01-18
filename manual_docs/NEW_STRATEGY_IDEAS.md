# 💡 Propuestas de Nuevas Estrategias para PST

Actualmente el sistema cubre bien los regímenes clásicos (Tendencia, Rango, Volatilidad). Sin embargo, hay dos "huecos" tácticos que podrían aumentar la rentabilidad, especialmente en índices y giros de mercado.

## 1. PST-Session-Master (Opening Range Breakout - ORB)
**Ideal para:** US500, EU50, NAS100 (Índices).
**Lógica:** Capturar la explosión de volatilidad en la apertura de mercados (Londres 09:00, NY 15:30).

*   **Funcionamiento:**
    1.  Marcar el Máximo y Mínimo de los primeros 15 minutos de la sesión (M15).
    2.  Si el precio rompe este rango con fuerza (Volumen > Media):
        *   **Entrada:** A favor de la ruptura.
        *   **Stop Loss:** En el punto medio del rango.
        *   **Take Profit:** 1.5x o 2x el tamaño del rango.
*   **Por qué añadirla:** Las estrategias actuales de tendencia a veces tardan en reaccionar a la apertura. Esta es específica para ese momento de alta oportunidad.

## 2. PST-Divergence-Hunter (Cazador de Divergencias)
**Ideal para:** Forex (EURUSD), Oro (XAUUSD), Cripto.
**Lógica:** Detectar giros de mercado antes de que ocurran, usando discrepancias entre Precio y RSI.

*   **Funcionamiento:**
    *   **Divergencia Alcista:** El precio hace un Mínimo Más Bajo (Lower Low), pero el RSI hace un Mínimo Más Alto (Higher Low). -> Señal de Compra.
    *   **Divergencia Bajista:** El precio hace un Máximo Más Alto (Higher High), pero el RSI hace un Máximo Más Bajo (Lower High). -> Señal de Venta.
*   **Por qué añadirla:** Actualmente `Range-Sniper` usa niveles fijos de RSI (30/70). Las divergencias son señales mucho más potentes y profesionales para detectar agotamiento de tendencia.

## 3. PST-News-Fade (Contra-Noticia)
**Ideal para:** Todos los activos tras noticias de alto impacto (NFP, CPI).
**Lógica:** Operar la reversión tras un pico irracional de volatilidad.

*   **Funcionamiento:**
    1.  Detectar una vela M5 inusualmente grande (> 3x ATR).
    2.  Esperar a que el precio se "frene" (patrón Harami o Doji).
    3.  Entrar en dirección contraria al pico (reversión a la media).

---
### Recomendación
Empezaríamos implementando **PST-Session-Master (ORB)**, ya que es mecánica, fácil de programar y complementa perfectamente a tus actuales estrategias de índices. ¿Te interesa?
