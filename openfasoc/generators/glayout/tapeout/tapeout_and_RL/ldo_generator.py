import sys
import argparse
from os import path, environ, rename
from pathlib import Path
from tempfile import TemporaryDirectory
from subprocess import Popen

environ['OPENBLAS_NUM_THREADS'] = '1'

# Path to glayout
sys.path.append(path.join(str(Path(__file__).resolve().parents[2])))

import numpy as np
import gdsfactory as gf
from gdsfactory.component import Component
from gdsfactory.components.rectangle import rectangle

from glayout.flow.pdk.mappedpdk import MappedPDK
from glayout.flow.pdk.sky130_mapped import sky130_mapped_pdk as pdk
from glayout.flow.pdk.util.comp_utils import movey, movex, align_comp_to_port, prec_ref_center
from glayout.flow.pdk.util.port_utils import add_ports_perimeter
from glayout.flow.routing.L_route import L_route
from glayout.flow.routing.straight_route import straight_route
from glayout.flow.primitives.fet import pmos
from glayout.flow.primitives.mimcap import mimcap_array
from glayout.flow.primitives.via_gen import via_array

# import from single opamp generator
from glayout.flow.blocks.composite.ldo.opamp_single_stage import opamp_singlestage

global PDK_ROOT
if 'PDK_ROOT' in environ:
    PDK_ROOT = str(Path(environ['PDK_ROOT']).resolve())
else:
    PDK_ROOT = "/usr/local/share/pdk/"

# ==========================================
# generating Resistor and Capacitor components for LDO feedback and compensation
# ==========================================
def create_resistor_divider(pdk: MappedPDK, width: float = 2.0, length: float = 20.0) -> Component:
    """gengerating feedback resistors R2 and R3 for the LDO, with a straight route connecting them and proper port definitions."""
    res_comp = Component(name="res_divider")
    r_layer = pdk.get_glayer("met3") 
    r2 = res_comp << rectangle(size=(width, length), layer=r_layer, centered=True)
    r3 = res_comp << rectangle(size=(width, length), layer=r_layer, centered=True)
    
    r3.movey(r2.ymin - length/2 - 2) 
    
    # connect R2 and R3 with a straight route and define ports
    res_comp << straight_route(pdk, r2.ports["e4"], r3.ports["e2"])
    
    # redefine ports for clarity
    res_comp.add_port("vreg_in", port=r2.ports["e2"])  # 顶端接 VReg (North)
    res_comp.add_port("vfb_out", port=r3.ports["e3"])  # 抽头接 VFB (West)
    res_comp.add_port("gnd_out", port=r3.ports["e4"])  # 底端接 GND (South)
    return res_comp


# ==========================================
# LDO Latout Generator
# ==========================================
def sky130_ldo_generator(
    pdk: MappedPDK,
    pass_fet_width: float = 21.8,
    pass_fet_length: float = 0.15,
    pass_fet_fingers: int = 1,
    pass_fet_mults: int = 1,
    opamp_diff_params: tuple = (20.7, 1, 10),
    opamp_bias_params: tuple = (5, 1, 1),
    opamp_load_params: tuple = (7, 1, 1),
    rmult: int = 2
) -> Component:
    ldo_top = Component(name="SKY130_LDO")

    # 1. Instantiation Error Amplifier (Single-Stage Opamp)
    print("Generating Error Amplifier...")
    ea_comp = opamp_singlestage(
        pdk=pdk,
        half_diffpair_params=opamp_diff_params,
        diffpair_bias=opamp_bias_params,
        half_pload=opamp_load_params,
        rmult=rmult
    )
    ea_ref = ldo_top << ea_comp
    
    # 2. Instantiation Pass FET (PMOS) - 2x multiplier for improved drive strength, with proper port alignment to EA output
    print(f"Generating PMOS Pass FET (W={pass_fet_width}, M={pass_fet_mults})...")
    pass_fet_comp = pmos(
        pdk, 
        width=pass_fet_width, 
        length=pass_fet_length, 
        fingers=pass_fet_fingers, 
        multipliers=pass_fet_mults,
        with_tie=True
    )
    pass_fet_ref = ldo_top << pass_fet_comp
    pass_fet_ref.movey(ea_ref.ymax + 40)
    
    # 3. Instantiation Feedback Network (Resistor Divider)
    print("Generating Feedback Network...")
    res_divider = create_resistor_divider(pdk)
    res_ref = ldo_top << res_divider
    res_ref.movex(ea_ref.xmax + 40).movey(ea_ref.ymin + 20)

    # 4. Instantiation Compensation Capacitor
    print("Generating Compensation Network...")
    comp_cap = mimcap_array(pdk, rows=2, columns=2, size=(10, 10))
    cap_ref = ldo_top << comp_cap
    cap_ref.movex(ea_ref.xmin - 40).movey(ea_ref.ymax)

    # ==========================================
    # Routing Internal Nodes
    # ==========================================
    print("Routing internal LDO nodes...")
    
    # A. connect EA_out to Pass FET Gate
    ea_out_port = ea_ref.ports["pin_vout_e2"] 
    pass_gate_port = pass_fet_ref.ports["multiplier_0_gate_W"] 
    
    ldo_top << L_route(pdk, ea_out_port, pass_gate_port, vglayer="met3", hglayer="met4")

    # B. Pass FET S/D Pads
    pass_src = pass_fet_ref.ports["multiplier_0_source_N"]
    pass_drn = pass_fet_ref.ports["multiplier_0_drain_N"]
    
    vdd_pad = ldo_top << rectangle(size=(10,5), layer=pdk.get_glayer("met4"), centered=True)
    vdd_pad.move(pass_src.center).movey(10)
    ldo_top << straight_route(pdk, pass_src, vdd_pad.ports["e4"])

    vreg_pad = ldo_top << rectangle(size=(10,5), layer=pdk.get_glayer("met4"), centered=True)
    vreg_pad.move(pass_drn.center).movey(10)
    ldo_top << straight_route(pdk, pass_drn, vreg_pad.ports["e4"])

    # C. VReg to top of Resistor Divider Input
    ldo_top << L_route(pdk, vreg_pad.ports["e1"], res_ref.ports["vreg_in"])

    # D. VFB from resistor divider to EA negative input
    ea_minus_port = ea_ref.ports["pin_minus_e4"] 
    ldo_top << L_route(pdk, res_ref.ports["vfb_out"], ea_minus_port, hglayer="met3", vglayer="met3")

    # E. compensation cap connections and routing
    cap_port_names = list(cap_ref.ports.keys())
    top_port_name = next((p for p in cap_port_names if "top" in p.lower() and "N" in p), None)
    bot_port_name = next((p for p in cap_port_names if "bottom" in p.lower() and "S" in p), None)

    if not top_port_name or not bot_port_name:
        raise ValueError(f"Cannot find suitable capacitor ports. Available: {cap_port_names}")

    ldo_top << L_route(pdk, cap_ref.ports[top_port_name], pass_gate_port, vglayer="met3", hglayer="met4")
    ldo_top << L_route(pdk, cap_ref.ports[bot_port_name], vreg_pad.ports["e3"], vglayer="met3", hglayer="met4")

    ldo_top.add_ports(vdd_pad.get_ports_list(), prefix="LDO_VDD_")
    ldo_top.add_ports(vreg_pad.get_ports_list(), prefix="LDO_VREG_")
    
    return ldo_top


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SKY130 LDO Layout Generator Utility")
    subparsers = parser.add_subparsers(title="mode", required=True, dest="mode")

    gen_ldo_parser = subparsers.add_parser("gen_ldo", help="Generate LDO layout (GDS).")
    gen_ldo_parser.add_argument("--pass_width", type=float, default=21.8, help="Pass FET Width")
    gen_ldo_parser.add_argument("--pass_mults", type=int, default=1, help="Pass FET Multipliers")
    gen_ldo_parser.add_argument("--output_gds", type=str, default="sky130_ldo.gds", help="Output GDS")

    args = parser.parse_args()

    if args.mode == "gen_ldo":
        print("====== Starting LDO Layout Generation ======")
        ldo = sky130_ldo_generator(
            pdk=pdk,
            pass_fet_width=args.pass_width,
            pass_fet_mults=args.pass_mults
        )
        ldo.show()
        ldo.write_gds(args.output_gds)
        print(f"====== Layout successfully saved to {args.output_gds} ======")