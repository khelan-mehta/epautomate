"""
DOE-2.2 SIM File Parser
Extracts energy metrics from DOE-2.2 .SIM output files.
"""

import re
from pathlib import Path


MONTH_NAMES = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
    5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
    9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}
MONTH_ABBR_TO_NUM = {v: k for k, v in MONTH_NAMES.items()}
MONTH_ABBR_LIST = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                   "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _floats(line):
    """Extract all floating-point numbers from a line."""
    return [float(x) for x in re.findall(r"[-+]?\d+\.?\d*", line)]


def _last_float(line):
    """Return the last float on a line, or None."""
    nums = _floats(line)
    return nums[-1] if nums else None


class SIMParser:
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.lines = []
        self.results = {}

    def parse(self):
        with open(self.filepath, "r", errors="replace") as f:
            self.lines = f.readlines()

        self._parse_header()
        self._parse_ps_b()
        self._parse_ps_e()
        self._parse_ss_d()
        self._parse_ss_r()
        self._parse_lv_b()
        self._parse_lv_c()
        self._parse_sv_a()
        self._compute_derived()
        return self.results

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    def _parse_header(self):
        r = self.results
        line1 = self.lines[0] if self.lines else ""
        r["project_name"] = line1[:65].strip()

        ts_m = re.search(r"(\d+/\d+/\d{4})\s+(\d+:\d+:\d+)", line1)
        if ts_m:
            r["timestamp"] = ts_m.group(1) + " " + ts_m.group(2)
        else:
            r["timestamp"] = ""

        # Weather file – search first 20 lines
        r["weather_file"] = ""
        for line in self.lines[:20]:
            wm = re.search(r"WEATHER FILE-\s+(.+?)(?:\s{2,}|$)", line)
            if wm:
                r["weather_file"] = wm.group(1).strip()
                break

    # ------------------------------------------------------------------
    # PS-B  Utility and Fuel Use Summary
    # ------------------------------------------------------------------
    def _parse_ps_b(self):
        r = self.results
        # Defaults
        r["kwh_annual"] = 0.0
        r["max_kw"] = 0.0
        r["peak_elec_mon_day"] = ""
        r["therm_annual"] = 0.0
        r["max_therm_hr"] = 0.0

        in_psb = False
        in_em1 = False
        in_fm1 = False
        after_em1_maxkw = False

        for line in self.lines:
            if "REPORT- PS-B" in line:
                in_psb = True
                in_em1 = False
                in_fm1 = False
                after_em1_maxkw = False
                continue

            if not in_psb:
                continue

            # Detect end of PS-B (next REPORT)
            if re.match(r"REPORT-\s", line) and "PS-B" not in line:
                in_psb = False
                continue

            stripped = line.strip()

            if stripped.startswith("EM1"):
                in_em1 = True
                in_fm1 = False
                after_em1_maxkw = False
                continue

            if stripped.startswith("FM1"):
                in_fm1 = True
                in_em1 = False
                continue

            if in_em1:
                if stripped.startswith("KWH"):
                    nums = _floats(stripped.replace("KWH", ""))
                    if nums:
                        r["kwh_annual"] = nums[-1]
                elif stripped.startswith("MAX KW"):
                    nums = _floats(stripped.replace("MAX KW", ""))
                    if nums:
                        r["max_kw"] = nums[-1]
                    after_em1_maxkw = True
                elif after_em1_maxkw and stripped.startswith("DAY/HR"):
                    # Last token is annual MON/DAY, e.g. "7/25"
                    tokens = stripped.split()
                    if tokens:
                        r["peak_elec_mon_day"] = tokens[-1]
                    after_em1_maxkw = False
                    in_em1 = False

            if in_fm1:
                if stripped.startswith("THERM"):
                    nums = _floats(stripped.replace("THERM", ""))
                    if nums:
                        r["therm_annual"] = nums[-1]
                elif stripped.startswith("MAX THERM/HR"):
                    nums = _floats(stripped.replace("MAX THERM/HR", ""))
                    if nums:
                        r["max_therm_hr"] = nums[-1]
                    in_fm1 = False

    # ------------------------------------------------------------------
    # PS-E  Energy End-Use Summary
    # ------------------------------------------------------------------
    def _parse_ps_e(self):
        r = self.results
        # Indices: 0=LIGHTS, 2=MISC EQUIP, 3=SPACE HTG, 4=SPACE CLG,
        #          5=HEAT REJECT, 6=PUMPS&AUX, 7=VENT FANS,
        #         10=DOMEST HOT WTR, 11=EXT USAGE, 12=TOTAL
        defaults_elec = {
            "ps_e_lights_kwh": 0.0, "ps_e_misc_kwh": 0.0,
            "ps_e_htg_kwh": 0.0, "ps_e_clg_kwh": 0.0,
            "ps_e_heat_reject_kwh": 0.0, "ps_e_pumps_kwh": 0.0,
            "ps_e_fans_kwh": 0.0, "ps_e_dhw_kwh": 0.0,
            "ps_e_ext_kwh": 0.0, "ps_e_total_kwh": 0.0,
        }
        defaults_fuel = {
            "ps_e_htg_mbtu": 0.0, "ps_e_dhw_mbtu": 0.0,
            "ps_e_total_mbtu": 0.0,
        }
        r.update(defaults_elec)
        r.update(defaults_fuel)

        # We look for the annual totals line (after "=======" separator)
        # in the PS-E Electric section
        in_pse_elec = False
        in_pse_fuel = False
        after_sep_elec = False
        after_sep_fuel = False

        for line in self.lines:
            # CONTINUED pages keep the same section active — don't reset state
            is_continued = "CONTINUED" in line

            if "REPORT- PS-E Energy End-Use Summary for all Electric" in line:
                if not is_continued:
                    after_sep_elec = False
                in_pse_elec = True
                in_pse_fuel = False
                continue
            if "REPORT- PS-E Energy End-Use Summary for all Fuel" in line:
                if not is_continued:
                    after_sep_fuel = False
                in_pse_fuel = True
                in_pse_elec = False
                continue

            # End detection – a new, non-PS-E report ends PS-E sections
            if re.match(r"REPORT-\s", line) and "PS-E" not in line:
                in_pse_elec = False
                in_pse_fuel = False
                continue

            stripped = line.strip()

            if in_pse_elec:
                if "=======" in stripped:
                    after_sep_elec = True
                    continue
                if after_sep_elec and stripped.startswith("KWH"):
                    nums = _floats(stripped.replace("KWH", ""))
                    if len(nums) >= 13:
                        r["ps_e_lights_kwh"] = nums[0]
                        r["ps_e_misc_kwh"] = nums[2]
                        r["ps_e_htg_kwh"] = nums[3]
                        r["ps_e_clg_kwh"] = nums[4]
                        r["ps_e_heat_reject_kwh"] = nums[5]
                        r["ps_e_pumps_kwh"] = nums[6]
                        r["ps_e_fans_kwh"] = nums[7]
                        r["ps_e_dhw_kwh"] = nums[10]
                        r["ps_e_ext_kwh"] = nums[11]
                        r["ps_e_total_kwh"] = nums[12]
                    in_pse_elec = False

            if in_pse_fuel:
                if "=======" in stripped:
                    after_sep_fuel = True
                    continue
                if after_sep_fuel and stripped.startswith("MBTU"):
                    nums = _floats(stripped.replace("MBTU", ""))
                    if len(nums) >= 13:
                        r["ps_e_htg_mbtu"] = nums[3]
                        r["ps_e_dhw_mbtu"] = nums[10]
                        r["ps_e_total_mbtu"] = nums[12]
                    in_pse_fuel = False

    # ------------------------------------------------------------------
    # SS-D  Building HVAC Load Summary
    # ------------------------------------------------------------------
    def _parse_ss_d(self):
        r = self.results
        r["peak_cooling_kbtuh"] = 0.0
        r["peak_heating_kbtuh"] = 0.0
        r["peak_elec_kw"] = 0.0
        r["peak_cooling_time"] = ""
        r["peak_heating_time"] = ""

        in_ssd = False
        monthly_rows = []

        for line in self.lines:
            if "REPORT- SS-D" in line:
                in_ssd = True
                monthly_rows = []
                continue

            if not in_ssd:
                continue

            if re.match(r"REPORT-\s", line) and "SS-D" not in line:
                in_ssd = False
                continue

            stripped = line.strip()

            # Monthly data rows: start with 3-letter month abbreviation
            month_m = re.match(
                r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(.+)$",
                stripped,
            )
            if month_m:
                mon_name = month_m.group(1)
                rest = month_m.group(2)
                nums = _floats(rest)
                # Expected: clg_energy, dy, hr, dry, wet, max_clg,
                #           htg_energy, dy, hr, dry, wet, max_htg, elec_kwh, max_kw
                if len(nums) >= 14:
                    monthly_rows.append({
                        "month": mon_name,
                        "clg_energy": nums[0],
                        "clg_dy": int(nums[1]),
                        "clg_hr": int(nums[2]),
                        # nums[3]=dry, nums[4]=wet for cooling
                        "max_clg": nums[5],
                        "htg_energy": nums[6],
                        "htg_dy": int(nums[7]),
                        "htg_hr": int(nums[8]),
                        # nums[9]=dry, nums[10]=wet for heating
                        "max_htg": abs(nums[11]),
                        "elec_kwh": nums[12],
                        "max_kw": nums[13],
                    })
                continue

            # MAX row
            if stripped.startswith("MAX"):
                nums = _floats(stripped.replace("MAX", ""))
                if len(nums) >= 3:
                    r["peak_cooling_kbtuh"] = abs(nums[0])
                    r["peak_heating_kbtuh"] = abs(nums[1])
                    r["peak_elec_kw"] = abs(nums[2])

        # Find peak times from monthly rows
        if monthly_rows:
            # Peak cooling month
            best_clg = max(monthly_rows, key=lambda x: x["max_clg"])
            mn = best_clg["month"]
            dy = best_clg["clg_dy"]
            hr = best_clg["clg_hr"]
            r["peak_cooling_time"] = f"{mn} {dy:02d} {hr:02d}:00"

            # Peak heating month
            best_htg = max(monthly_rows, key=lambda x: x["max_htg"])
            mn = best_htg["month"]
            dy = best_htg["htg_dy"]
            hr = best_htg["htg_hr"]
            r["peak_heating_time"] = f"{mn} {dy:02d} {hr:02d}:00"

        # Peak electrical time from PS-B data (peak_elec_mon_day like "7/25")
        # plus look at SS-D for the hour from the month
        if "peak_elec_mon_day" in r and r["peak_elec_mon_day"]:
            parts = r["peak_elec_mon_day"].split("/")
            if len(parts) == 2:
                try:
                    mon_num = int(parts[0])
                    day = int(parts[1])
                    mon_name = MONTH_NAMES.get(mon_num, "")
                    # Find hour from monthly_rows
                    hr = 0
                    for row in monthly_rows:
                        if row["month"] == mon_name:
                            hr = row["clg_hr"] if row["max_clg"] > 0 else row["htg_hr"]
                            break
                    if mon_name:
                        r["peak_elec_time"] = f"{mon_name} {day:02d} {hr:02d}:00"
                except (ValueError, IndexError):
                    r["peak_elec_time"] = ""
            else:
                r["peak_elec_time"] = ""
        else:
            r["peak_elec_time"] = ""

    # ------------------------------------------------------------------
    # SS-R  Zone Performance Summary (unmet hours)
    # ------------------------------------------------------------------
    def _parse_ss_r(self):
        r = self.results
        r["unmet_heating_hrs"] = 0
        r["unmet_cooling_hrs"] = 0

        for line in self.lines:
            m = re.match(r"^\s+TOTAL\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", line)
            if m:
                r["unmet_heating_hrs"] += int(m.group(3))
                r["unmet_cooling_hrs"] += int(m.group(4))

    # ------------------------------------------------------------------
    # LV-B  Summary of Spaces
    # ------------------------------------------------------------------
    def _parse_lv_b(self):
        r = self.results
        r["total_area"] = 0.0
        r["total_volume"] = 0.0
        r["total_people"] = 0.0
        r["conditioned_area"] = 0.0
        r["conditioned_people"] = 0.0
        r["lpd_sum"] = 0.0       # sum of lights * area for conditioned spaces
        r["epd_sum"] = 0.0       # sum of equip * area for conditioned spaces
        r["spaces"] = []

        # Parse BUILDING TOTALS for total area
        for line in self.lines:
            if line.startswith("BUILDING TOTALS"):
                # Format: BUILDING TOTALS  <people>  <area>  <volume>
                # (some columns like LIGHTS and EQUIP are blank in the totals row)
                nums = _floats(line.replace("BUILDING TOTALS", ""))
                if len(nums) >= 3:
                    r["total_people"] = nums[0]
                    r["total_area"] = nums[1]
                    r["total_volume"] = nums[2]
                elif len(nums) == 2:
                    r["total_area"] = nums[0]
                    r["total_volume"] = nums[1]
                break

        # Parse individual space lines
        # Pattern: name (up to col ~40), MULT, TYPE, AZIM, LIGHTS, PEOPLE, EQUIP,
        #          INF_METHOD, ACH, AREA, VOLUME
        space_pattern = re.compile(
            r"^(.{1,50}?)\s{2,}([\d.]+)\s+(EXT|INT)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+\S+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
        )

        for line in self.lines:
            m = space_pattern.match(line)
            if m:
                name = m.group(1).strip()
                mult = float(m.group(2))
                space_type = m.group(3)
                lights = float(m.group(5))
                people = float(m.group(6))
                equip = float(m.group(7))
                area = float(m.group(9))
                volume = float(m.group(10))

                sp = {
                    "name": name,
                    "mult": mult,
                    "type": space_type,
                    "lights": lights,
                    "people": people,
                    "equip": equip,
                    "area": area * mult,
                    "volume": volume * mult,
                    "conditioned": lights > 0 or people > 0,
                }
                r["spaces"].append(sp)

                if sp["conditioned"]:
                    r["conditioned_area"] += sp["area"]
                    r["conditioned_people"] += people * mult
                    r["lpd_sum"] += lights * sp["area"]
                    r["epd_sum"] += equip * sp["area"]

        if not r["total_area"] and r["spaces"]:
            r["total_area"] = sum(s["area"] for s in r["spaces"])

    # ------------------------------------------------------------------
    # LV-C  Details of Space (windows, walls, U-values)
    # ------------------------------------------------------------------
    def _parse_lv_c(self):
        r = self.results
        # Window data: area-weighted averages
        r["window_data"] = []
        # Exterior wall data
        r["ext_wall_data"] = []
        # Underground / slab data
        r["underground_data"] = []
        # Roof data
        r["roof_data"] = []

        in_lvc = False
        in_windows = False
        in_ext_surfaces = False
        in_underground = False

        win_pattern = re.compile(
            r"^\s+(\S.+?)\s{2,}([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$"
        )
        ext_wall_pattern = re.compile(
            r"^\s+(\S.+?)\s{2,}([\d.]+)\s+([\d.]+)\s+(\S+)\s+([\d.]+)\s+(DELAYED|QUICK|STANDARD)\s*"
        )
        underground_pattern = re.compile(
            r"^\s+(\S.+?)\s{2,}([\d.]+)\s+([\d.]+)\s+(\S.+?)\s{2,}([\d.]+)\s*$"
        )

        for line in self.lines:
            if "REPORT- LV-C" in line:
                in_lvc = True
                in_windows = False
                in_ext_surfaces = False
                in_underground = False
                continue

            if not in_lvc:
                continue

            if re.match(r"REPORT-\s", line) and "LV-C" not in line:
                in_lvc = False
                continue

            stripped = line.strip()

            if "EXTERIOR WINDOWS" in line:
                in_windows = True
                in_ext_surfaces = False
                in_underground = False
                continue

            if "EXTERIOR SURFACES" in line:
                in_ext_surfaces = True
                in_windows = False
                in_underground = False
                continue

            if "UNDERGROUND SURFACES" in line:
                in_underground = True
                in_windows = False
                in_ext_surfaces = False
                continue

            if "INTERIOR SURFACES" in line or "PEOPLE" in line or "LIGHTING" in line:
                in_windows = False
                # keep ext/underground flags

            if in_windows and stripped and not stripped.startswith("WINDOW") and not stripped.startswith("GLASS"):
                # Try to parse window line
                m = re.match(
                    r"^\s+(\S.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
                    line,
                )
                if m:
                    mult = float(m.group(2))
                    area = float(m.group(3)) * mult
                    u_val = float(m.group(8))
                    sc = float(m.group(9))
                    vlt = float(m.group(10))
                    solar_trans = float(m.group(11))
                    shgc = sc * 0.87
                    lsg = vlt / shgc if shgc > 0 else 0.0
                    r["window_data"].append({
                        "area": area,
                        "u_value": u_val,
                        "sc": sc,
                        "shgc": shgc,
                        "vlt": vlt,
                        "lsg": lsg,
                    })
                    continue

            if in_ext_surfaces and stripped and not stripped.startswith("SURFACE"):
                # Format: NAME  MULT  AREA  CONSTRUCTION  U-VALUE  TYPE
                m = re.match(
                    r"^\s+(\S.+?)\s+([\d.]+)\s+([\d.]+)\s+(\S.+?)\s+([\d.]+)\s+(DELAYED|QUICK)\s*",
                    line,
                )
                if m:
                    area = float(m.group(3)) * float(m.group(2))
                    u_val = float(m.group(5))
                    r["ext_wall_data"].append({"area": area, "u_value": u_val})
                    continue

            if in_underground and stripped and not stripped.startswith("SURFACE"):
                # Format: NAME  MULT  AREA  CONSTRUCTION  U-VALUE
                m = re.match(
                    r"^\s+(\S.+?)\s+([\d.]+)\s+([\d.]+)\s+(\S.+?)\s+([\d.]+)\s*$",
                    line,
                )
                if m:
                    area = float(m.group(3)) * float(m.group(2))
                    u_val = float(m.group(5))
                    r["underground_data"].append({"area": area, "u_value": u_val})
                    continue

        # Compute weighted averages
        total_win_area = sum(w["area"] for w in r["window_data"])
        if total_win_area > 0:
            r["glass_u_value"] = sum(w["u_value"] * w["area"] for w in r["window_data"]) / total_win_area
            r["glass_shgc"] = sum(w["shgc"] * w["area"] for w in r["window_data"]) / total_win_area
            r["glass_vlt"] = sum(w["vlt"] * w["area"] for w in r["window_data"]) / total_win_area
            r["glass_lsg"] = r["glass_vlt"] / r["glass_shgc"] if r["glass_shgc"] > 0 else 0.0
            r["total_window_area"] = total_win_area
        else:
            r["glass_u_value"] = 0.0
            r["glass_shgc"] = 0.0
            r["glass_vlt"] = 0.0
            r["glass_lsg"] = 0.0
            r["total_window_area"] = 0.0

        total_wall_area = sum(w["area"] for w in r["ext_wall_data"])
        if total_wall_area > 0:
            r["wall_u_value"] = sum(w["u_value"] * w["area"] for w in r["ext_wall_data"]) / total_wall_area
            r["total_wall_area"] = total_wall_area
        else:
            r["wall_u_value"] = 0.0
            r["total_wall_area"] = 0.0

        total_ug_area = sum(u["area"] for u in r["underground_data"])
        if total_ug_area > 0:
            r["slab_u_value"] = sum(u["u_value"] * u["area"] for u in r["underground_data"]) / total_ug_area
        else:
            r["slab_u_value"] = 0.0

    # ------------------------------------------------------------------
    # SV-A  System Design Parameters (fan CFM)
    # ------------------------------------------------------------------
    def _parse_sv_a(self):
        r = self.results
        r["total_supply_cfm"] = 0.0
        r["total_oa_cfm"] = 0.0
        r["total_fan_kw"] = 0.0
        r["cooling_eir_list"] = []
        r["heating_eir_list"] = []

        in_sva = False
        after_fan_header = False
        supply_cfm_found = False

        for line in self.lines:
            if "REPORT- SV-A" in line:
                in_sva = True
                after_fan_header = False
                supply_cfm_found = False
                continue

            if not in_sva:
                continue

            if re.match(r"REPORT-\s", line) and "SV-A" not in line:
                in_sva = False
                continue

            stripped = line.strip()

            # Fan line: "  SUPPLY   XXXX.  ..."
            # In SV-A, the fan data line is like:
            #   SUPPLY     863.       1.00    0.236   ...
            if re.match(r"SUPPLY\s+[\d.]+", stripped):
                nums = _floats(stripped.replace("SUPPLY", ""))
                if nums:
                    r["total_supply_cfm"] += nums[0]
                    if len(nums) >= 3:
                        r["total_fan_kw"] += nums[2]
                continue

            # System EIR line (contains COOLING EIR and HEATING EIR columns)
            # PSZ  1.010  2038.3  10.  0.198  29.527  0.674  -55.971  0.346  0.000  ...
            if re.match(r"^(PSZ|PVAVS?|VAVS?|FPFC?|SZRH|PTAC|PTHP|HVSYS)\s", stripped):
                nums = _floats(stripped)
                # cols: alt_factor, area, people, oa_ratio, clg_cap, shr, htg_cap,
                #        clg_eir, htg_eir, supp_heat
                if len(nums) >= 10:
                    clg_eir = nums[7]
                    htg_eir = nums[8]
                    if clg_eir > 0:
                        r["cooling_eir_list"].append(1.0 / clg_eir)
                    if htg_eir > 0:
                        r["heating_eir_list"].append(1.0 / htg_eir)
                # Outside air CFM approximation: OA_RATIO * supply_cfm
                # We get OA CFM from zone lines
                continue

            # Zone supply flow line in SV-A:
            # "ZONE_NAME  supply_cfm  exhaust_cfm  fan_kw  min_frac  oa_cfm  ..."
            if stripped and not stripped.startswith("ZONE") and not stripped.startswith("FAN") \
               and not stripped.startswith("SYSTEM") and "SUPPLY" not in stripped \
               and "EXHAUST" not in stripped:
                # Check if it looks like a zone data line
                zone_m = re.match(
                    r"^(.+?)\s+([\d.]+)\.\s+([\d.]+)\.\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\.",
                    line,
                )
                if zone_m:
                    oa_cfm = float(zone_m.group(6))
                    r["total_oa_cfm"] += oa_cfm

        # DX cooling / heating COP
        if r["cooling_eir_list"]:
            r["dx_cooling_cop"] = sum(r["cooling_eir_list"]) / len(r["cooling_eir_list"])
        else:
            r["dx_cooling_cop"] = 0.0

        if r["heating_eir_list"]:
            r["dx_heating_cop"] = sum(r["heating_eir_list"]) / len(r["heating_eir_list"])
        else:
            r["dx_heating_cop"] = 0.0

    # ------------------------------------------------------------------
    # Derived / computed metrics
    # ------------------------------------------------------------------
    def _compute_derived(self):
        r = self.results

        KWH_TO_KBTU = 3.412
        THERM_TO_KBTU = 100.0
        MBTU_TO_KBTU = 1000.0

        # --- Energy totals ---
        r["electricity_kbtu"] = r["kwh_annual"] * KWH_TO_KBTU
        r["gas_kbtu"] = r["therm_annual"] * THERM_TO_KBTU

        # End-use breakdowns from PS-E
        r["int_lighting_kbtu"] = r["ps_e_lights_kwh"] * KWH_TO_KBTU
        r["int_equip_kbtu"] = r["ps_e_misc_kwh"] * KWH_TO_KBTU
        r["htg_elec_kbtu"] = r["ps_e_htg_kwh"] * KWH_TO_KBTU
        r["clg_elec_kbtu"] = r["ps_e_clg_kwh"] * KWH_TO_KBTU
        r["heat_reject_kbtu"] = r["ps_e_heat_reject_kwh"] * KWH_TO_KBTU
        r["pumps_kbtu"] = r["ps_e_pumps_kwh"] * KWH_TO_KBTU
        r["fans_kbtu"] = r["ps_e_fans_kwh"] * KWH_TO_KBTU
        r["water_sys_elec_kbtu"] = r["ps_e_dhw_kwh"] * KWH_TO_KBTU
        r["ext_lighting_kbtu"] = r["ps_e_ext_kwh"] * KWH_TO_KBTU

        r["htg_gas_kbtu"] = r["ps_e_htg_mbtu"] * MBTU_TO_KBTU
        r["water_sys_gas_kbtu"] = r["ps_e_dhw_mbtu"] * MBTU_TO_KBTU

        r["total_energy_kbtu"] = r["electricity_kbtu"] + r["gas_kbtu"]

        # --- Areas ---
        total_area = r.get("total_area", 0.0)
        conditioned_area = r.get("conditioned_area", 0.0)
        if conditioned_area == 0:
            conditioned_area = total_area

        r["conditioned_floor_area"] = conditioned_area
        r["total_floor_area"] = total_area

        # --- Intensities ---
        ref_area = total_area if total_area > 0 else 1.0
        r["eui_kbtu_ft2"] = r["total_energy_kbtu"] / ref_area

        # --- Peaks ---
        # Peak electrical in W = max_kw * 1000
        r["peak_elec_w"] = r.get("peak_elec_kw", r.get("max_kw", 0.0)) * 1000.0
        peak_elec_w_per_ft2 = r["peak_elec_w"] / ref_area if ref_area > 0 else 0.0
        r["peak_elec_w_per_ft2"] = peak_elec_w_per_ft2

        peak_clg = r.get("peak_cooling_kbtuh", 0.0)
        peak_htg = r.get("peak_heating_kbtuh", 0.0)
        r["peak_cooling_btuh_ft2"] = peak_clg * 1000.0 / ref_area if ref_area > 0 else 0.0
        r["peak_heating_btuh_ft2"] = peak_htg * 1000.0 / ref_area if ref_area > 0 else 0.0

        # --- LPD / EPD ---
        cond_area = conditioned_area if conditioned_area > 0 else 1.0
        r["lpd_w_ft2"] = r.get("lpd_sum", 0.0) / cond_area
        r["epd_w_ft2"] = r.get("epd_sum", 0.0) / cond_area
        r["lpd_total_w_ft2"] = r.get("lpd_sum", 0.0) / ref_area
        r["epd_total_w_ft2"] = r.get("epd_sum", 0.0) / ref_area

        # --- Occupant density ---
        total_people = r.get("total_people", 0.0)
        cond_people = r.get("conditioned_people", 0.0)
        r["occ_density_ft2_person"] = total_area / total_people if total_people > 0 else 0.0
        r["cond_occ_density_ft2_person"] = conditioned_area / cond_people if cond_people > 0 else 0.0

        # --- WWR ---
        total_win_area = r.get("total_window_area", 0.0)
        total_wall_area = r.get("total_wall_area", 0.0)
        r["building_wwr"] = (total_win_area / total_wall_area * 100.0) if total_wall_area > 0 else 0.0

        # --- Wall R-value ---
        wu = r.get("wall_u_value", 0.0)
        r["wall_r_value"] = 1.0 / wu if wu > 0 else 0.0

        # --- Airflow ---
        supply_cfm = r.get("total_supply_cfm", 0.0)
        r["vent_cfm_per_ft2"] = supply_cfm / ref_area if ref_area > 0 else 0.0

        # --- DHW efficiency placeholder ---
        r["dhw_efficiency"] = 0.0

        # --- Ext lighting KW from PS-B data ---
        # max kw for ext lighting – not directly available; leave zero
        r["ext_lighting_kw"] = 0.0


if __name__ == "__main__":
    import json, sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        parser = SIMParser(path)
        results = parser.parse()
        # Print key metrics
        for k, v in sorted(results.items()):
            if not isinstance(v, (list, dict)):
                print(f"{k}: {v}")
