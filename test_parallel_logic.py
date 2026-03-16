import asyncio
import os
import sys

# Mocking
class MockPosition:
    def __init__(self, comment):
        self.comment = comment

async def test_categorized_parallelism():
    print("--- TESTING CATEGORIZED PARALLELISM LOGIC ---")
    
    # STRATEGY CATEGORIES from config.py
    STRATEGIES = {
        "PST-EMA-Flow": "CORE",
        "PST-Mean-Reversion": "CORE",
        "PST-Scalper-Pro": "SCALPING"
    }
    
    def check_can_open(new_strat, current_positions):
        new_cat = STRATEGIES.get(new_strat, "CORE")
        
        # Count current by category
        counts = {"CORE": 0, "SCALPING": 0}
        for p in current_positions:
            p_name = p.comment.replace("PST_", "")
            p_cat = STRATEGIES.get(p_name, "CORE")
            counts[p_cat] += 1
            
        if counts[new_cat] >= 1:
            return False, f"Blocked: Already have a {new_cat} position."
        return True, "Allowed"

    # CASE 1: EMA-Flow open, try Mean-Reversion
    pos1 = [MockPosition("PST_PST-EMA-Flow")]
    can, msg = check_can_open("PST-Mean-Reversion", pos1)
    print(f"Case 1 (EMA -> MeanRev): {'✅ FAIL (Correctly Blocked)' if not can else '❌ ERROR (Allowed)'} - {msg}")

    # CASE 2: EMA-Flow open, try Scalper
    can, msg = check_can_open("PST-Scalper-Pro", pos1)
    print(f"Case 2 (EMA -> Scalper): {'✅ PASS (Allowed)' if can else '❌ ERROR (Blocked)'} - {msg}")

    # CASE 3: Scalper open, try EMA-Flow
    pos2 = [MockPosition("PST_PST-Scalper-Pro")]
    can, msg = check_can_open("PST-EMA-Flow", pos2)
    print(f"Case 3 (Scalper -> EMA): {'✅ PASS (Allowed)' if can else '❌ ERROR (Blocked)'}")

if __name__ == "__main__":
    asyncio.run(test_categorized_parallelism())
