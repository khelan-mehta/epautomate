"""
CLI entry point for DOE-2.2 SIM file extractor.

Usage:
    python sim_extractor/run.py --bl file1.SIM [file2.SIM ...] \
        [--bl-inp file1.inp file2.inp ...] \
        [--proposed file3.SIM ...] \
        [--proposed-inp file3.inp ...] \
        [--option "Name"] \
        [--climate-zone 4A] \
        [--output results.xlsx] \
        [--config config.json]

If a .inp file is supplied alongside a .SIM file, additional inputs
(utility rates, shading, glass codes, site/building params) are parsed
from it and merged into the result row. Pairing is by position.
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python sim_extractor/run.py` from repo root
sys.path.insert(0, str(Path(__file__).parent))

from sim_parser import SIMParser
from inp_parser import INPParser
from excel_writer import write_workbook


def _auto_inp_for(sim_path):
    """Find a .inp file in the same dir as the .SIM (by stem or by name match)."""
    p = Path(sim_path)
    # First try same stem
    candidate = p.with_suffix(".inp")
    if candidate.exists():
        return candidate
    # eQUEST sometimes drops the " - Baseline Design" suffix on .inp
    stem = p.stem
    for suffix in (" - Baseline Design", "_Baseline_Design"):
        if stem.endswith(suffix):
            shortened = p.parent / (stem[:-len(suffix)] + ".inp")
            if shortened.exists():
                return shortened
    # Fall back to any .inp in the same folder
    inps = list(p.parent.glob("*.inp"))
    return inps[0] if len(inps) == 1 else None


def parse_args():
    ap = argparse.ArgumentParser(
        description="Extract DOE-2.2 SIM data to Excel"
    )
    ap.add_argument(
        "--bl", nargs="+", metavar="SIM_FILE",
        help="Baseline SIM file(s)"
    )
    ap.add_argument(
        "--bl-inp", nargs="+", metavar="INP_FILE", default=[],
        help="Baseline .inp file(s), paired by position with --bl"
    )
    ap.add_argument(
        "--proposed", nargs="+", metavar="SIM_FILE",
        help="Proposed SIM file(s)"
    )
    ap.add_argument(
        "--proposed-inp", nargs="+", metavar="INP_FILE", default=[],
        help="Proposed .inp file(s), paired by position with --proposed"
    )
    ap.add_argument(
        "--no-auto-inp", action="store_true",
        help="Disable auto-discovery of .inp files in same folder as .SIM"
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


def process_run(sim_file, option_name, climate_zone, inp_file=None, auto_inp=True):
    """Parse a single SIM file (+ optional paired .inp) and return result dict."""
    path = Path(sim_file)
    print(f"  Parsing SIM: {path.name} ...", end=" ", flush=True)
    parser = SIMParser(path)
    results = parser.parse()
    results["option_name"] = option_name or path.stem
    results["results_path"] = str(path)
    results["climate_zone"] = climate_zone
    print("done.")

    # Locate paired .inp file
    inp_path = None
    if inp_file:
        inp_path = Path(inp_file)
    elif auto_inp:
        inp_path = _auto_inp_for(path)

    if inp_path and inp_path.exists():
        print(f"  Parsing INP: {inp_path.name} ...", end=" ", flush=True)
        try:
            inp_data = INPParser(inp_path).parse()
            # Merge .inp data — don't overwrite SIM-derived values, but
            # promote known utility rates to the canonical column names.
            results.update(inp_data)
            if inp_data.get("inp_elec_rate_per_kbtu") is not None:
                results.setdefault("elec_rate_per_kbtu", inp_data["inp_elec_rate_per_kbtu"])
            if inp_data.get("inp_gas_rate_per_kbtu") is not None:
                results.setdefault("gas_rate_per_kbtu", inp_data["inp_gas_rate_per_kbtu"])
            results["inp_file"] = str(inp_path)
            print("done.")
        except Exception as e:
            print(f"FAILED ({e})")
    elif inp_file:
        print(f"  WARNING: .inp file not found: {inp_file}")

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
                inp_file=run.get("inp_file"),
                auto_inp=not args.no_auto_inp,
            )
            if tab == "bl":
                bl_rows.append(r)
            else:
                proposed_rows.append(r)
        output = cfg.get("output", args.output)
    else:
        bl_files = args.bl or []
        bl_inps = args.bl_inp or []
        proposed_files = args.proposed or []
        proposed_inps = args.proposed_inp or []

        def pair(sims, inps):
            for i, s in enumerate(sims):
                yield s, (inps[i] if i < len(inps) else None)

        print("Processing Baseline runs:")
        bl_rows = []
        for i, (f, inp) in enumerate(pair(bl_files, bl_inps)):
            label = f"{args.option} BL {i+1}" if len(bl_files) > 1 else args.option
            bl_rows.append(process_run(f, label, args.climate_zone,
                                       inp_file=inp,
                                       auto_inp=not args.no_auto_inp))

        print("Processing Proposed runs:")
        proposed_rows = []
        for i, (f, inp) in enumerate(pair(proposed_files, proposed_inps)):
            label = f"{args.option} Prop {i+1}" if len(proposed_files) > 1 else args.option
            proposed_rows.append(process_run(f, label, args.climate_zone,
                                             inp_file=inp,
                                             auto_inp=not args.no_auto_inp))

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
        if "inp_file" in r:
            print(f"\n--- From .inp file: {Path(r['inp_file']).name} ---")
            print(f"  Elec rate ($/kWh):    {r.get('inp_elec_rate_per_kwh', '—')}")
            print(f"  Elec rate ($/kBtu):   {r.get('inp_elec_rate_per_kbtu', '—')}")
            print(f"  Gas rate ($/therm):   {r.get('inp_gas_rate_per_therm', '—')}")
            print(f"  Elec monthly chg ($): {r.get('inp_elec_monthly_chg', 0):.2f}")
            print(f"  Master elec meter:    {r.get('inp_master_elec_meter', '')}")
            print(f"  Master fuel meter:    {r.get('inp_master_fuel_meter', '')}")
            print(f"  Site altitude (ft):   {r.get('inp_altitude_ft', '')}")
            print(f"  Run period:           {r.get('inp_run_period', '')}")
            print(f"  Glass type codes:     {r.get('inp_glass_type_code_list', '')}")
            for d in ("n", "e", "s", "w"):
                t = r.get(f"inp_{d}_shading_type", "")
                pct = r.get(f"inp_{d}_shading_ratio", 0)
                print(f"  Shading {d.upper()}:            {t}  ({pct}%)")


if __name__ == "__main__":
    main()
