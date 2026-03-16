import MetaTrader5 as mt5
from datetime import datetime, timedelta

def dump_pst_deals():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return
        
    from_date = datetime.now() - timedelta(days=7)
    deals = mt5.history_deals_get(from_date, datetime.now())
    
    if deals is None:
        print("No deals found")
    else:
        print(f"--- Recent Deals with PST Comments ({len(deals)} total deals) ---")
        count = 0
        for d in deals:
            if d.comment and ("PST" in d.comment or "HEDGE" in d.comment):
                print(f"Ticket/Pos: {d.position_id} | Deal: {d.ticket} | Sym: {d.symbol} | Comment: '{d.comment}'")
                count += 1
        print(f"Found {count} PST-related deals.")
            
    mt5.shutdown()

if __name__ == "__main__":
    dump_pst_deals()
