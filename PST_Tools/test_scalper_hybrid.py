import pandas as pd
import numpy as np
import asyncio
from PST_Core.strategies.pst_scalper_pro import PSTScalperPro

async def test_hybrid():
    scalper = PSTScalperPro()
    
    # --- ESCENARIO 1: REVERSIÓN (Suelo Bollinger) ---
    df_rev = pd.DataFrame({
        'close': [100.0] * 50,
        'high': [100.5] * 50,
        'low': [99.5] * 50,
        'tick_volume': [100] * 50
    })
    # Tocar banda inferior (BBL será ~99.5, nosotros bajamos precio a 99.4)
    df_rev.loc[49, 'close'] = 99.4
    df_rev.loc[49, 'low'] = 99.3
    
    mtf_rev = {'m1': df_rev, 'm5': df_rev}
    res_rev = await scalper.calculate_signal(mtf_rev)
    
    print("\n[SCENARIO 1] REVERSIÓN:")
    print(f"  - Signal: {res_rev['signal']} | Score: {res_rev['score']} | Concept: {res_rev['metadata']['mode']}")
    print(f"  - TP Target: {res_rev['tp_price']:.5f} (BBM)")

    # --- ESCENARIO 2: MOMENTUM (Ruptura de EMA Cluster) ---
    # Creamos un DF donde las EMAs están alineadas (21 > 50) y el precio rompe arriba
    # EMA 21 será aprox 100, EMA 50 será aprox 99
    closes = ([98.0] * 30) + ([101.0] * 20) # Salto brusco para alinear EMAs
    df_mom = pd.DataFrame({
        'close': closes,
        'high': [c + 0.1 for c in closes],
        'low': [c - 0.1 for c in closes],
        'tick_volume': [1000] * 50 # Alto volumen
    })
    # Forzar ruptura en la última vela
    df_mom.loc[48, 'close'] = 99.5 # Estaba debajo de la EMA 21 (~100)
    df_mom.loc[49, 'close'] = 100.5 # Rompe hacia arriba
    
    mtf_mom = {'m1': df_mom, 'm5': df_mom}
    res_mom = await scalper.calculate_signal(mtf_mom)
    
    print("\n[SCENARIO 2] MOMENTUM/IMPULSO:")
    print(f"  - Signal: {res_mom['signal']} | Score: {res_mom['score']} | Concept: {res_mom['metadata']['mode']}")
    print(f"  - TP Target: {res_mom['tp_price']:.5f} (BBU)")

if __name__ == "__main__":
    asyncio.run(test_hybrid())
