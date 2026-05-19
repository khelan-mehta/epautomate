"""
CLI entry point for DOE-2.2 SIM file extractor.

Usage:
    python sim_extractor/run.py --bl file1.SIM [file2.SIM ...] \
        [--proposed file3.SIM ...] \
        [--option "Name"] \
        [--climate-zone 4A] \
        [--output results.xlsx] \
        [--config config.json]
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python sim_extractor/run.py` from repo root
sys.path.insert(0, str(Path(__file__).parent))

from sim_parser import SIMParser
from excel_writer import write_workbook


def parse_args():
    ap = argparse.ArgumentParser(
        description="Extract DOE-2.2 SIM data to Excel"
    )
    ap.add_argument(
        "--bl", nargs="+", metavar="SIM_FILE",
        help="Baseline SIM file(s)"
    )
    ap.add_argument(
        "--proposed", nargs="+", metavar="SIM_FILE",
        help="Proposed SIM file(s)"
    )
    ap.add_argument(
        "--option", default="",
        help="Option name label"
    )
    ap.add_argument(
        "--climate-zone", default="",
        help="Climate zone (e.g. 4A)"
    )
    ap.add_argument(
        "--output", default="energy_results.xlsx",
        help="Output Excel file path"
    )
    ap.add_argument(
        "--config", default=None,
        help="JSON config file (overrides other args)"
    )
    return ap.parse_args()


def process_run(sim_file, option_name, climate_zone):
    """Parse a single SIM file and return a result dict."""
    path = Path(sim_file)
    print(f"  Parsing: {path.name} ...", end=" ", flush=True)
    parser = SIMParser(path)
    results = parser.parse()
    results["option_name"] = option_name or path.stem
    results["results_path"] = str(path)
    results["climate_zone"] = climate_zone
    print("done.")
    return results


def main():
    args = parse_args()

    # --- Load config if provided ---
    rates = {}
    if args.config:
        with open(args.config) as f:
            cfg = json.load(f)
        rates = cfg.get("rates", {})
        bl_rows = []
        proposed_rows = []
        for run in cfg.get("runs", []):
            tab = run.get("tab", "bl").lower()
            r = process_run(
                run["sim_file"],
                run.get("option_name", ""),
                run.get("climate_zone", ""),
            )
            if tab == "bl":
                bl_rows.append(r)
            else:
                proposed_rows.append(r)
        output = cfg.get("output", args.output)
    else:
        bl_files = args.bl or []
        proposed_files = args.proposed or []

        print("Processing Baseline runs:")
        bl_rows = []
        for i, f in enumerate(bl_files):
            label = f"{args.option} BL {i+1}" if len(bl_files) > 1 else args.option
            bl_rows.append(process_run(f, label, args.climate_zone))

        print("Processing Proposed runs:")
        proposed_rows = []
        for i, f in enumerate(proposed_files):
            label = f"{args.option} Prop {i+1}" if len(proposed_files) > 1 else args.option
            proposed_rows.append(process_run(f, label, args.climate_zone))

        output = args.output

    print(f"\nWriting Excel workbook: {output}")
    write_workbook(output, bl_rows, proposed_rows, rates=rates)

    # Print summary of key extracted values
    if bl_rows:
        r = bl_rows[0]
        print("\n--- Key Extracted Values (BL Run 1) ---")
        print(f"  Project:              {r.get('project_name', '')}")
        print(f"  Timestamp:            {r.get('timestamp', '')}")
        print(f"  Weather file:         {r.get('weather_file', '')}")
        print(f"  Total area (ft²):     {r.get('total_floor_area', 0):,.0f}")
        print(f"  Conditioned area:     {r.get('conditioned_floor_area', 0):,.0f}")
        print(f"  Electricity kBtu:     {r.get('electricity_kbtu', 0):,.0f}")
        print(f"  Gas kBtu:             {r.get('gas_kbtu', 0):,.0f}")
        print(f"  Total energy kBtu:    {r.get('total_energy_kbtu', 0):,.0f}")
        print(f"  EUI (kBtu/ft²):       {r.get('eui_kbtu_ft2', 0):.1f}")
        print(f"  Peak cooling kBtuh:   {r.get('peak_cooling_kbtuh', 0):,.1f}")
        print(f"  Peak heating kBtuh:   {r.get('peak_heating_kbtuh', 0):,.1f}")
        print(f"  Peak elec KW:         {r.get('max_kw', 0):,.1f}")
        print(f"  Unmet htg hrs:        {r.get('unmet_heating_hrs', 0)}")
        print(f"  Unmet clg hrs:        {r.get('unmet_cooling_hrs', 0)}")
        print(f"  Int lighting kBtu:    {r.get('int_lighting_kbtu', 0):,.0f}")
        print(f"  Int equip kBtu:       {r.get('int_equip_kbtu', 0):,.0f}")
        print(f"  Htg gas kBtu:         {r.get('htg_gas_kbtu', 0):,.0f}")
        print(f"  Water sys gas kBtu:   {r.get('water_sys_gas_kbtu', 0):,.0f}")
        print(f"  Fans kBtu:            {r.get('fans_kbtu', 0):,.0f}")
        print(f"  Clg elec kBtu:        {r.get('clg_elec_kbtu', 0):,.0f}")
        print(f"  Glass U-value:        {r.get('glass_u_value', 0):.3f}")
        print(f"  Glass SHGC:           {r.get('glass_shgc', 0):.3f}")
        print(f"  Glass VLT:            {r.get('glass_vlt', 0):.3f}")
        print(f"  Wall U-value:         {r.get('wall_u_value', 0):.3f}")
        print(f"  Slab U-value:         {r.get('slab_u_value', 0):.3f}")
        print(f"  LPD (W/ft²):          {r.get('lpd_w_ft2', 0):.2f}")
        print(f"  EPD (W/ft²):          {r.get('epd_w_ft2', 0):.2f}")
        print(f"  Total supply CFM:     {r.get('total_supply_cfm', 0):,.0f}")
        print(f"  Building WWR (%):     {r.get('building_wwr', 0):.1f}")
        print(f"  Peak cooling time:    {r.get('peak_cooling_time', '')}")
        print(f"  Peak heating time:    {r.get('peak_heating_time', '')}")
        print(f"  Peak elec time:       {r.get('peak_elec_time', '')}")


if __name__ == "__main__":
    main()
