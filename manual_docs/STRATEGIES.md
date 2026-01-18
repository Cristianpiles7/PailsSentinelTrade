# 📘 Documentación de Estrategias PST

Este documento detalla la lógica interna, indicadores y sistemas de puntuación de todas las estrategias activas en el Bot PST.

---

## 1. Estrategia Híbrida (Manual + Auto)
Esta es la estrategia principal que ves en el Dashboard como **"Channel Master PST"** o **"Niveles Manuales"**.

### **PST-Channel-Master (Gestor de Niveles)**
**Tipo:** Híbrida (Reversión/Ruptura)  
**Objetivo:** Operar reacciones del precio en niveles clave (Soportes, Resistencias, Líneas de Tendencia).

#### **Lógica de Zonas (Scoring 0-100)**
El sistema clasifica cada nivel manual en 3 zonas según la distancia del precio actual:

1.  **Zona NEUTRAL (Puntuación: 0)**
    *   **Distancia:** > 0.25%
    *   **Estado:** El precio está lejos. El bot ignora este nivel para ahorrar recursos.
    *   **Acción:** Ninguna.

2.  **Zona VIGILAR (Puntuación: 10 - 45)**
    *   **Distancia:** Entre 0.05% y 0.25%
    *   **Estado:** El precio se acerca. Se activa el monitoreo de alta frecuencia.
    *   **Cálculo:** `Score = 10 + (Distancia Inversa * Factor)`
    *   **Acción:** Preparar gatillos, pero no disparar aún.

3.  **Zona ACCIÓN (Puntuación: 50 - 100)**
    *   **Distancia:** < 0.05% (Muy cerca o tocando)
    *   **Estado:** Nivel "caliente". Se evalúan gatillos de entrada.
    *   **Gatillos:**
        *   **REBOTE (Bounce):** Si el precio toca y se gira (ej. Toca Resistencia y baja).
            *   *Bonus Excelencia:* +15 pts si RSI confirma (Sobrecompra en Resistencia).
        *   **ROTURA (Breakout):** Si el precio cruza el nivel con fuerza.
            *   *Bonus Excelencia:* +15 pts si Volumen > Media Móvil.

---

## 2. Estrategias Automáticas (Sub-Estrategias)
El **Clasificador de Régimen** (RegimeClassifier) decide cuál de estas estrategias activar según el estado del mercado (Rango, Tendencia o Volatilidad).

### **A. PST-Range-Sniper (Francotirador de Rango)**
**Régimen:** RANGE (Lateral)  
**Lógica:** Buscar reversiones a la media (Mean Reversion). Compra barato, vende caro.

*   **Indicadores:** Bandas de Bollinger (2.5 std), RSI (14), Williams %R.
*   **Sistema de Puntuación:**
    *   **Extremos (40 pts):** Precio tocando Banda Superior/Inferior.
    *   **Sobre-extensión (30 pts):** RSI en extremos (>70 o <30).
    *   **Patrón de Vela (30 pts):** Detecta patrones de reversión (Pinbar, Engulfing).
*   **Señal de Entrada:** Score > 75 + Confirmación de dirección opuesta a la banda.

### **B. PST-Trend-Elite (Tendencia Macro)**
**Régimen:** TREND (Tendencial)  
**Lógica:** Seguir la tendencia fuerte establecida en H1 y confirmada en M15.

*   **Indicadores:** EMAs (21, 50, 200), ADX, VWAP.
*   **Sistema de Puntuación:**
    *   **Estructura H1 (40 pts):** EMAs alineadas perfectamente (21 > 50 > 200 para Bull).
    *   **Momentum M15 (20 pts):** ADX > 25 (Fuerza de tendencia).
    *   **Alineación M15 (10 pts):** Precio por encima de EMA 21.
    *   **Disparo M5 (30 pts):** Precio cruzando VWAP a favor de la tendencia.
*   **Señal de Entrada:** Score >= 60.

### **C. PST-Trend-Pullback (Retrocesos)**
**Régimen:** TREND (Tendencial)  
**Lógica:** Entrar a favor de tendencia pero en "descuentos" (retrocesos), no en máximos.

*   **Indicadores:** EMAs (21, 50, 200), RSI (14).
*   **Sistema de Puntuación:**
    *   **Estructura (40 pts):** Tendencia clara definida por EMAs.
    *   **Zona de Valor (30 pts):** El precio retrocede y toca la EMA 21 (la "Zona de Valor").
    *   **RSI Pullback (30 pts):** RSI se "enfría" durante la tendencia (ej. baja a 40 en una tendencia alcista).
*   **Señal de Entrada:** Score >= 70 (Requiere Estructura + Pullback confirmado).

### **D. PST-Vol-Breakout (Ruptura de Volatilidad)**
**Régimen:** VOLATILE (Alta Volatilidad / Explosivo)  
**Lógica:** Capturar movimientos explosivos tras periodos de compresión.

*   **Indicadores:** Bandas de Bollinger, ADX.
*   **Sistema de Puntuación:**
    *   **ADX Power (40 pts):** ADX > 40 (Super Tendencia).
    *   **Breakout (40 pts):** Precio cierra fuera de las Bandas de Bollinger.
    *   **Squeeze (20 pts):** Bandas se comprimieron antes de la explosión.
*   **Señal de Entrada:** Score >= 80 (Requiere ADX extremo + Ruptura clara).

---

## Resumen de Uso en Dashboard
*   **Botón "🤖 ANALIZAR ESTRATEGIA":** Muestra los datos de la sección 2 (Range, Trend, Pullback o Volatility) y el estado global.
*   **Botón "📏 ANALIZAR NIVEL":** Muestra los datos de la sección 1 (Channel Master) y tus líneas manuales.
