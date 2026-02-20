import multiprocessing
import sys
import os
import time

# Añadir el directorio raíz al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def run_bot():
    """Ejecuta el núcleo del Pails Sentinel Trade."""
    from PST_Core.run_pst import start_v6, SYMBOLS_TO_TRADE
    import asyncio
    print("🚀 [SISTEMA] Arrancando Motor de Trading PST...")
    asyncio.run(start_v6(SYMBOLS_TO_TRADE))

def run_dashboard():
    """Ejecuta el servidor del Dashboard."""
    from PST_Core.dashboard.server import app
    print("📊 [DASHBOARD] Arrancando Interfaz Web en http://0.0.0.0:5000")
    # Desactivamos el reloader para evitar conflictos con multiprocessing
    app.run(debug=False, port=5000, host='0.0.0.0')

if __name__ == "__main__":
    print("\n" + "="*50)
    print("💎 PAILS SENTINEL TRADE - UNIFIED MASTER LAUNCHER 💎")
    print("="*50 + "\n")

    # Inicializar Base de Datos antes de lanzar procesos
    from PST_Core.models.database import PSTDatabase
    import asyncio
    asyncio.run(PSTDatabase().initialize())

    # Crear procesos independientes
    bot_process = multiprocessing.Process(target=run_bot)
    dashboard_process = multiprocessing.Process(target=run_dashboard)

    try:
        # Iniciar Dashboard primero
        dashboard_process.start()
        time.sleep(2) # Dar un momento al servidor para respirar
        
        # Iniciar Motor de Trading
        bot_process.start()

        # Mantener el script vivo mientras ambos procesos corran
        while True:
            time.sleep(1)
            if not bot_process.is_alive() or not dashboard_process.is_alive():
                print("⚠️ Uno de los procesos se ha detenido. Cerrando sistema...")
                break

    except KeyboardInterrupt:
        print("\n🛑 [SISTEMA] Apagado solicitado por el usuario...")
    finally:
        bot_process.terminate()
        dashboard_process.terminate()
        print("✅ Sistema cerrado correctamente.\n")
