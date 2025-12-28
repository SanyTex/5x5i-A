import argparse
import time
from config.settings import SETTINGS
from src.common.log import info, warn
from src.papertrader.engine import run_papertrader_loop

from src.papertrader import exits_A_404020 as A
from src.papertrader import exits_B_25252525 as B
from src.papertrader import exits_C_fib as C

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=[
        "PT_A_FINAL_404020",
        "PT_B_COMPARE_25252525",
        "PT_C_EXPERIMENT_FIB"
    ])
    args = parser.parse_args()

    if args.variant == "PT_A_FINAL_404020":
        pt_tag = "PT_A_FINAL_404020"
        pt_dir = "data/paper/PT_A_FINAL_404020"
        exits_mod = A
    elif args.variant == "PT_B_COMPARE_25252525":
        pt_tag = "PT_B_COMPARE_25252525"
        pt_dir = "data/paper/PT_B_COMPARE_25252525"
        exits_mod = B
    else:
        pt_tag = "PT_C_EXPERIMENT_FIB"
        pt_dir = "data/paper/PT_C_EXPERIMENT_FIB"
        exits_mod = C

    # Dauerschleife
    while True:
        try:
            run_papertrader_loop(pt_tag, pt_dir, exits_mod)
        except Exception as e:
            warn(f"{pt_tag} crashed: {e} -> restart in 10s")
            time.sleep(10)

if __name__ == "__main__":
    main()
