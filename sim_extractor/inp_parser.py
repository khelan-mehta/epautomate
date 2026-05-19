"""
DOE-2.2 .inp (BDL input) file parser.

Extracts inputs that are NOT echoed into the .SIM output, primarily:
- Utility rates ($/kWh, $/therm, monthly charges) from UTILITY-RATE +
  BLOCK-CHARGE objects
- External shading (overhangs and fins) per WINDOW, aggregated by facade
- Glass type codes referenced by windows
- Site parameters (altitude, latitude)
- Building parameters (azimuth, holiday schedule)
- Master meter assignments (which EM/FM meters drive utility cost)
- Project title and run period

BDL syntax:
    "Object Name" = OBJECT-TYPE
       PROP-NAME        = value
       LIST-PROP        = ( a, b, c )
       ..
Each block ends with `..`.  Lines beginning with `$` are comments.
"""

import re
from pathlib import Path
from collections import defaultdict


# Map an azimuth angle (deg, 0=N, clockwise) to a cardinal direction
def _azimuth_to_facade(az):
    az = az % 360
    if az < 45 or az >= 315:    return "N"
    if az < 135:                return "E"
    if az < 225:                return "S"
    return "W"


class INPParser:
    """Parse a DOE-2.2 .inp BDL file into a structured dict."""

    # Match a block header like:    "Name" = OBJECT-TYPE
    _HDR_RE = re.compile(
        r'^\s*"([^"]+)"\s*=\s*([A-Z][A-Z0-9\-]+)\s*$'
    )
    # Bare keyword block (e.g.  TITLE, PROJECT-DATA) on its own line
    _BARE_HDR_RE = re.compile(
        r'^\s*([A-Z][A-Z0-9\-]+)\s*$'
    )
    # Property line like:    KEY              = value
    _PROP_RE = re.compile(
        r'^\s*([A-Z][A-Z0-9\-]+)\s*=\s*(.+?)\s*$'
    )

    def __init__(self, inp_path):
        self.path = Path(inp_path)
        self.text = self.path.read_text(errors="ignore") if self.path.exists() else ""
        self.blocks = []                 # list of (name, type, props_dict)
        self._by_type = defaultdict(list)  # type -> list of blocks
        self._by_name = {}               # name -> block

    # ──────────────────────────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────────────────────────
    def parse(self):
        """Parse the .inp file and return a flat dict of extracted values."""
        if not self.text:
            return {}

        self._parse_blocks()

        out = {}
        out.update(self._extract_project())
        out.update(self._extract_site_building())
        out.update(self._extract_meters())
        out.update(self._extract_utility_rates())
        out.update(self._extract_glass_types())
        out.update(self._extract_shading())
        return out

    # ──────────────────────────────────────────────────────────────
    #  Block tokenizer
    # ──────────────────────────────────────────────────────────────
    def _parse_blocks(self):
        """Walk the file line-by-line and emit (name, type, props) tuples."""
        name = None
        otype = None
        props = {}
        in_block = False

        def commit():
            nonlocal name, otype, props, in_block
            if otype is not None:
                self.blocks.append((name, otype, props))
                self._by_type[otype].append((name, props))
                if name:
                    self._by_name[name] = (otype, props)
            name = None
            otype = None
            props = {}
            in_block = False

        for raw in self.text.splitlines():
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("$"):
                continue

            # End of block
            if line.strip() == "..":
                commit()
                continue

            # Property line with trailing `..`
            if line.rstrip().endswith(".."):
                line = line[:line.rfind("..")].rstrip()

            # Named block header
            m = self._HDR_RE.match(line)
            if m:
                if in_block:
                    commit()
                name = m.group(1)
                otype = m.group(2)
                props = {}
                in_block = True
                continue

            # Bare-keyword block header (TITLE, PROJECT-DATA, etc.)
            m = self._BARE_HDR_RE.match(line)
            if m and not in_block:
                otype = m.group(1)
                name = None
                props = {}
                in_block = True
                continue

            # Property assignment
            m = self._PROP_RE.match(line)
            if m and in_block:
                key, val = m.group(1), m.group(2)
                props[key] = self._clean_value(val)
                continue

        commit()  # flush trailing block

    @staticmethod
    def _clean_value(v):
        v = v.strip()
        # Quoted string
        if v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        # Parenthesized list:  ( a, b, c )
        if v.startswith("(") and v.endswith(")"):
            inner = v[1:-1].strip()
            items = [x.strip().strip('"') for x in inner.split(",")]
            # cast numeric items
            casted = []
            for x in items:
                try:
                    casted.append(float(x) if "." in x else int(x))
                except (ValueError, TypeError):
                    casted.append(x)
            return casted
        # Try number
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return v

    # ──────────────────────────────────────────────────────────────
    #  Extractors
    # ──────────────────────────────────────────────────────────────
    def _extract_project(self):
        """Title, run period, holidays."""
        out = {"inp_title": "", "inp_run_period": "", "inp_holidays": ""}
        for _name, props in self._by_type.get("TITLE", []):
            if "LINE-1" in props:
                out["inp_title"] = str(props["LINE-1"]).strip("*")
                break
        for _name, props in self._by_type.get("RUN-PERIOD-PD", []):
            bm = props.get("BEGIN-MONTH"); bd = props.get("BEGIN-DAY"); by = props.get("BEGIN-YEAR")
            em = props.get("END-MONTH");   ed = props.get("END-DAY");   ey = props.get("END-YEAR")
            if bm and em:
                out["inp_run_period"] = f"{bm}/{bd}/{by} – {em}/{ed}/{ey}"
            break
        for name, _props in self._by_type.get("HOLIDAYS", []):
            if name:
                out["inp_holidays"] = name
                break
        return out

    def _extract_site_building(self):
        """Altitude, latitude (if present), building azimuth."""
        out = {"inp_altitude_ft": None, "inp_building_azimuth": None}
        for _n, props in self._by_type.get("SITE-PARAMETERS", []):
            if "ALTITUDE" in props:
                out["inp_altitude_ft"] = props["ALTITUDE"]
            if "LATITUDE" in props:
                out["inp_latitude"] = props["LATITUDE"]
            if "LONGITUDE" in props:
                out["inp_longitude"] = props["LONGITUDE"]
            break
        for _n, props in self._by_type.get("BUILD-PARAMETERS", []):
            if "AZIMUTH" in props:
                out["inp_building_azimuth"] = props["AZIMUTH"]
            break
        return out

    def _extract_meters(self):
        """Master electric / fuel meter assignments."""
        out = {"inp_master_elec_meter": "", "inp_master_fuel_meter": ""}
        for _n, props in self._by_type.get("MASTER-METERS", []):
            out["inp_master_elec_meter"] = props.get("MSTR-ELEC-METER", "")
            out["inp_master_fuel_meter"] = props.get("MSTR-FUEL-METER", "")
            break
        return out

    def _extract_utility_rates(self):
        """
        Resolve UTILITY-RATE → BLOCK-CHARGE chain to get effective $/kBtu.

        For each UTILITY-RATE, sum its BLOCK-CHARGES' COSTS-1 (the first-tier
        block rate).  Convert by fuel type:
            ELECTRICITY  $/kWh   ÷ 3.412 → $/kBtu
            NATURAL-GAS  $/therm × 0.01  → $/kBtu (1 therm = 100 kBtu)
        Monthly charges are summed across all rates of that fuel.
        """
        out = {
            "inp_elec_rate_per_kwh":   None,
            "inp_elec_rate_per_kbtu":  None,
            "inp_gas_rate_per_therm":  None,
            "inp_gas_rate_per_kbtu":   None,
            "inp_elec_monthly_chg":    0.0,
            "inp_gas_monthly_chg":     0.0,
            "inp_utility_rates":       [],
        }

        for name, props in self._by_type.get("UTILITY-RATE", []):
            fuel = str(props.get("TYPE", "")).upper()
            monthly = props.get("MONTH-CHGS", 0)
            if isinstance(monthly, list):
                monthly = sum(x for x in monthly if isinstance(x, (int, float)))

            # Resolve block-charge references
            block_refs = props.get("BLOCK-CHARGES", [])
            if isinstance(block_refs, str):
                block_refs = [block_refs]

            tier1_cost = 0.0
            for ref in block_refs:
                if ref in self._by_name:
                    _otype, bprops = self._by_name[ref]
                    costs = bprops.get("COSTS-1", [])
                    if isinstance(costs, list) and costs:
                        # first-tier rate (typical for non-tiered utility)
                        try:
                            tier1_cost += float(costs[0])
                        except (TypeError, ValueError):
                            pass
                    elif isinstance(costs, (int, float)):
                        tier1_cost += float(costs)

            rec = {
                "name": name, "fuel": fuel,
                "monthly_charge": monthly, "tier1_cost": tier1_cost,
            }
            out["inp_utility_rates"].append(rec)

            if "ELEC" in fuel:
                out["inp_elec_rate_per_kwh"]  = tier1_cost
                out["inp_elec_rate_per_kbtu"] = tier1_cost / 3.412 if tier1_cost else None
                out["inp_elec_monthly_chg"]   += float(monthly or 0)
            elif "GAS" in fuel or fuel == "NATURAL-GAS":
                out["inp_gas_rate_per_therm"] = tier1_cost
                out["inp_gas_rate_per_kbtu"]  = tier1_cost / 100.0 if tier1_cost else None
                out["inp_gas_monthly_chg"]    += float(monthly or 0)

        return out

    def _extract_glass_types(self):
        """List of glass type codes referenced in the model."""
        codes = []
        names = []
        for name, props in self._by_type.get("GLASS-TYPE", []):
            names.append(name)
            code = props.get("GLASS-TYPE-CODE", "")
            if code:
                codes.append(str(code))
        return {
            "inp_glass_type_names": names,
            "inp_glass_type_codes": codes,
            "inp_glass_type_code_list": ", ".join(codes),
        }

    def _extract_shading(self):
        """
        Aggregate external shading (overhangs + fins) per facade.

        We walk every WINDOW block, determine the parent EXTERIOR-WALL's
        azimuth (from BUILD-PARAMETERS + wall's own AZIMUTH), and tally:
          - count of windows with OVERHANG-* defined
          - count with FIN-A / FIN-B defined
          - max overhang projection depth (OVERHANG-D)
        """
        bldg_az = 0.0
        for _n, props in self._by_type.get("BUILD-PARAMETERS", []):
            try:
                bldg_az = float(props.get("AZIMUTH", 0))
            except (TypeError, ValueError):
                pass
            break

        # Walk file once more to track wall→window parent relationships.
        # In BDL, WINDOWs inherit the most recently declared EXTERIOR-WALL.
        cur_wall_az = None
        per_facade = defaultdict(lambda: {
            "windows": 0, "shaded": 0, "overhang": 0, "fins": 0,
            "max_overhang_d": 0.0,
        })

        cur_block_type = None
        cur_block_props = {}
        for raw in self.text.splitlines():
            line = raw.strip()
            if not line or line.startswith("$"):
                continue
            if line == "..":
                if cur_block_type == "EXTERIOR-WALL":
                    try:
                        cur_wall_az = float(cur_block_props.get("AZIMUTH", 0)) + bldg_az
                    except (TypeError, ValueError):
                        cur_wall_az = bldg_az
                elif cur_block_type == "WINDOW" and cur_wall_az is not None:
                    facade = _azimuth_to_facade(cur_wall_az)
                    bucket = per_facade[facade]
                    bucket["windows"] += 1
                    has_oh = any(k.startswith("OVERHANG-") for k in cur_block_props)
                    has_fin = any(k in ("FIN-A", "FIN-B", "FIN-C", "FIN-D") for k in cur_block_props)
                    if has_oh:  bucket["overhang"] += 1
                    if has_fin: bucket["fins"]     += 1
                    if has_oh or has_fin: bucket["shaded"] += 1
                    try:
                        d = float(cur_block_props.get("OVERHANG-D", 0))
                        bucket["max_overhang_d"] = max(bucket["max_overhang_d"], d)
                    except (TypeError, ValueError):
                        pass
                cur_block_type = None
                cur_block_props = {}
                continue

            m = self._HDR_RE.match(line)
            if m:
                cur_block_type = m.group(2)
                cur_block_props = {}
                continue

            m = self._PROP_RE.match(line)
            if m and cur_block_type:
                cur_block_props[m.group(1)] = self._clean_value(m.group(2))

        out = {}
        for facade in ("N", "E", "S", "W"):
            b = per_facade[facade]
            shaded_pct = (b["shaded"] / b["windows"] * 100) if b["windows"] else 0.0
            out[f"inp_{facade.lower()}_shading_type"] = (
                "Overhang+Fin" if b["overhang"] and b["fins"]
                else "Overhang" if b["overhang"]
                else "Fin"      if b["fins"]
                else "None"
            )
            out[f"inp_{facade.lower()}_shading_ratio"] = round(shaded_pct, 1)
            out[f"inp_{facade.lower()}_max_overhang_ft"] = round(b["max_overhang_d"], 2)
        return out


# Convenience
def parse_inp(path):
    return INPParser(path).parse()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python inp_parser.py <file.inp>")
        sys.exit(1)
    import json
    result = parse_inp(sys.argv[1])
    print(json.dumps(result, indent=2, default=str))
