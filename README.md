# 💎 Pails Sentinel Trade (PST)

**Sistema de Trading Algorítmico Avanzado en Python + MetaTrader 5**

Este bot (anteriormente "Project PST") es un sistema automatizado que escanea el mercado, detecta estructuras de precios (Canales, Niveles Manuales, Tendencias) y ejecuta o sugiere operaciones basándose en un sistema de puntuación algorítmica.

## 🚀 Características Clave
*   **Análisis Multiamenaza:** Rango, Tendencia, Ruptura y Noticias.
*   **Estrategia Manual Híbrida:** Dibuja niveles en MT5 y el bot los gestiona (Alertas < 0.25%, Acción < 0.05%).
*   **Dashboard Web:** Interfaz gráfica en tiempo real (http://127.0.0.1:5000) para monitorización y control.

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
