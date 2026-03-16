import os
import signal
import psutil

def kill_process_by_name(script_name):
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline')
            if cmdline and script_name in " ".join(cmdline):
                print(f"Found process {proc.info['pid']} with cmdline: {cmdline}")
                proc.terminate()
                print(f"Process {proc.info['pid']} terminated.")
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

if __name__ == "__main__":
    if kill_process_by_name("PST_MASTER.py"):
        print("Successfully killed PST_MASTER.py")
    else:
        print("Could not find PST_MASTER.py process")
