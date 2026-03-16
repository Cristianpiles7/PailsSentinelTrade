import pandas as pd
import numpy as np
import asyncio
from PST_Core.strategies.pst_scalper_pro import PSTScalperPro

async def test_fuzzy():
    scalper = PSTScalperPro()
    
    # 1. Simular datos: Precio cerca de banda inferior pero no tocando
    # BB Std 1.8 en ventana 20
    # Creamos un DF con 50 velas estables y la última cayendo
    df = pd.DataFrame({
        'close': [100.0] * 50,
        'high': [100.5] * 50,
        'low': [99.5] * 50,
        'tick_volume': [1000] * 50
    })
    
    # Penúltima vela a 100. Última a 98.5 (Casi toca banda)
    df.loc[49, 'close'] = 98.5
    
    # Simular mtf_data
    mtf_data = {
        'm1': df,
        'm5': df # Simplificado
    }
    
    result = await scalper.calculate_signal(mtf_data)
    print(f"--- TEST 1: Cercanía a Banda ---")
    print(f"Score: {result['score']} | Signal: {result['signal']}")
    for f in result['metadata']['factors_detailed']:
        print(f"  - {f['k']}: {f['v']} -> {f['score']}")

    # 2. Simular RSI en 32 (Anteriormente 0 puntos, ahora Fuzzy puntos)
    # Forzamos RSI a 32 mediante manipulación de precios si es necesario, 
    # pero aquí podemos ver el impacto del código directamente.
    
    print("\n--- Conclusión ---")
    if result['score'] > 0:
        print("✅ El Fuzzy Scoring está funcionando (Score > 0 sin tocar banda exactamente)")
    else:
        print("❌ El Fuzzy Scoring no dio puntos.")

if __name__ == "__main__":
    asyncio.run(test_fuzzy())
