import sys

file_path = "c:\\Users\\crist\\Desktop\\BOLSA\\PailsSentinelTrade\\PailsSentinelTrade\\PST_Web\\src\\App.jsx"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = lines[:64] + lines[290:]

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("Sliced EquityCurve successfully.")
