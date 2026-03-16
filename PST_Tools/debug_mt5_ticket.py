import MetaTrader5 as mt5
from datetime import datetime, timedelta

def debug_ticket(ticket):
    if not mt5.initialize():
        print("MT5 Init Failed")
        return
        
    from_date = datetime.now() - timedelta(days=90)
    deals = mt5.history_deals_get(from_date, datetime.now(), position=ticket)
    
    print(f"--- Deals for position {ticket} ---")
    if deals is None:
        print("No deals found or error")
    else:
        for d in deals:
            print(f"Deal ID: {d.ticket} | Position: {d.position_id} | Type: {d.type} | Comment: '{d.comment}'")
            
    mt5.shutdown()

if __name__ == "__main__":
    debug_ticket(102493645)
