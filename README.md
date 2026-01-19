# 💎 Pails Sentinel Trade (PST)

**Sistema de Trading Algorítmico Avanzado en Python + MetaTrader 5**

Este bot (anteriormente "Project PST") es un sistema automatizado que escanea el mercado, detecta estructuras de precios (Canales, Niveles Manuales, Tendencias) y ejecuta o sugiere operaciones basándose en un sistema de puntuación algorítmica.

## 🚀 Características Clave
*   **Análisis Multiamenaza:** Rango, Tendencia, Ruptura y Noticias.
*   **Estrategia Manual Híbrida:** Dibuja niveles en MT5 y el bot los gestiona (Alertas < 0.25%, Acción < 0.05%).
*   **Dashboard Web:** Interfaz gráfica en tiempo real (http://127.0.0.1:5000) para monitorización y control.

## 🧠 Estrategias Activas

### 1. PST-Channel-Master (Ductilidad Táctica)
El núcleo del sistema. Utiliza **Canales de Regresión Lineal** para identificar si el precio está "caro" o "barato" respecto a su tendencia central.
*   **Lógica:** Compra en la banda inferior, Vende en la superior.
*   **Filtros:** Pendiente del canal (evita operar contra tendencias fuertes) y desviación estándar adaptativa.
*   **Niveles Manuales:** Si dibujas líneas en MT5, la estrategia las respeta y las usa como triggers de alta precisión.

### 2. PST-RSI-Equities (Francotirador de Rangos)
Diseñada para mercados laterales o activos volátiles en M1/M5.
*   **Trigger:** Eventos de entrada/salida de zonas de sobrecompra (>70) y sobreventa (<30).
*   **Confirmación:** Exige volumen superior a la media para validar el giro.
*   **Adaptable:** Ajusta sus umbrales automáticamente si opera Cripto (más volátil) o Forex/Acciones.

### 3. PST-EMA-Flow (Seguidor de Tendencia Puro)
Estrategia robusta para capturar grandes movimientos institucionales.
*   **Modo Estricto:** Solo dispara si detecta un evento (Cruce Dorado, Rebote Confirmado o Breakout) en las **últimas 3 velas**.
*   **Filtros de Acero:**
    *   **Jerarquía M15:** Solo opera si la tendencia de 15 minutos coincide.
    *   **Anti-FOMO:** Si el precio se aleja >0.3% de la media, anula la entrada.
    *   **Vela de Rechazo:** Para rebotes, exige que la vela cierre a favor de la tendencia (Verde en soporte, Roja en resistencia).

---

## 🛠️ Instalación (Desarrollador)

Si quieres editar el código o ejecutarlo desde Python:

1.  **Requisitos:**
    *   Python 3.10+ instalado.
    *   MetaTrader 5 Terminal instalado (Logueado en tu cuenta).
    *   Git (Opcional).

2.  **Configuración:**
    ```bash
    # 1. Clonar o descargar carpeta PailsSentinelTrade
    cd PailsSentinelTrade
    
    # 2. Crear entorno virtual (Recomendado)
    python -m venv .venv
    .venv\Scripts\activate
    
    # 3. Instalar dependencias
    pip install -r requirements.txt
    ```

3.  **Ejecución:**
    ```bash
    python PST_MASTER.py
    ```

---

## 📦 Modo "Fácil" (Para Amigos / Distribución)

Si quieres usar el bot sin instalar Python ni librerías:

1.  Ejecuta el script **`DISTRIBUTE_PST.bat`**.
    *   Esto creará una carpeta `dist/PST_MASTER/`.
2.  Copia esa carpeta entera y pásasela a tu amigo.
3.  Tu amigo solo tiene que abrir **`PST_MASTER.exe`** (dentro de la carpeta) y tener MT5 abierto.

---

## 📂 Estructura del Proyecto
*   `PST_MASTER.py`: Lanzador principal.
*   `PST_Core/`: Cerebro del bot (Estrategias, Motores).
*   `Tools/`: Scripts de utilidad y configuración.
*   `Utils/`: Librerías auxiliares.

© 2026 Pails Sentinel Trade. Uso bajo tu propia responsabilidad.
