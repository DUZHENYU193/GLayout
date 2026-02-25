import sys
from os import path, rename, environ
environ['OPENBLAS_NUM_THREADS'] = '1'
from pathlib import Path
# path to glayout
sys.path.append(path.join(str(Path(__file__).resolve().parents[5])))


from gdsfactory.cell import cell, clear_cache
from gdsfactory.component import Component, copy
from gdsfactory.component_reference import ComponentReference
from gdsfactory.components.rectangle import rectangle
from glayout.flow.pdk.mappedpdk import MappedPDK
from typing import Optional, Union
from glayout.flow.primitives.fet import nmos, pmos, multiplier
from glayout.flow.blocks.elementary.diff_pair import diff_pair
from glayout.flow.primitives.guardring import tapring
from glayout.flow.primitives.mimcap import mimcap_array, mimcap
from glayout.flow.routing.L_route import L_route
from glayout.flow.routing.c_route import c_route
from glayout.flow.primitives.via_gen import via_stack, via_array
from gdsfactory.routing.route_quad import route_quad
from glayout.flow.pdk.util.comp_utils import evaluate_bbox, prec_ref_center, movex, movey, to_decimal, to_float, move, align_comp_to_port, get_padding_points_cc
from glayout.flow.pdk.util.port_utils import rename_ports_by_orientation, rename_ports_by_list, add_ports_perimeter, print_ports, set_port_orientation, rename_component_ports
from glayout.flow.routing.straight_route import straight_route
from glayout.flow.pdk.util.snap_to_grid import component_snap_to_grid
from pydantic import validate_arguments
from glayout.flow.placement.two_transistor_interdigitized import two_nfet_interdigitized

from glayout.flow.blocks.composite.diffpair_cmirror_bias import diff_pair_ibias
from glayout.flow.blocks.composite.stacked_current_mirror import stacked_nfet_current_mirror
from glayout.flow.blocks.composite.differential_to_single_ended_converter import differential_to_single_ended_converter
# from glayout.flow.blocks.composite.opamp.row_csamplifier_diff_to_single_ended_converter import row_csamplifier_diff_to_single_ended_converter
from glayout.flow.blocks.composite.opamp.diff_pair_stackedcmirror import diff_pair_stackedcmirror
from glayout.flow.spice import Netlist
from glayout.flow.blocks.elementary.current_mirror import current_mirror_netlist


@validate_arguments
def __create_and_route_pins(
    pdk: MappedPDK,
    opamp_single_top: Component,
    pmos_comps_ref: ComponentReference
) -> tuple:
    _max_metal_seperation_ps = pdk.util_max_metal_seperation()

    # ==========================================
    # place and route VDD pin to PMOS load (2L/2R)
    # ==========================================
    vddpin = opamp_single_top << rectangle(size=(5,3), layer=pdk.get_glayer("met4"), centered=True)
    vddpin.movey(opamp_single_top.ymax)
    opamp_single_top << straight_route(pdk, opamp_single_top.ports["pcomps_2L2Rsrcvia_top_met_N"], vddpin.ports["e4"])

    # ==========================================
    # place and route bias pin for diff pair tail current (ibias)
    # ==========================================
    vbias1 = opamp_single_top << rectangle(size=(5,3), layer=pdk.get_glayer("met3"), centered=True)
    vbias1.movey(opamp_single_top.ymin - _max_metal_seperation_ps - vbias1.ymax)
    opamp_single_top << straight_route(pdk, vbias1.ports["e2"], opamp_single_top.ports["diffpair_ibias_B_gate_S"], width=1, fullbottom=False)

    # ==========================================
    # route the diff pair input pins (VIN+ and VIN-) with antenna violation mitigation
    # ==========================================
    # (VIN-)
    minusi_pin = opamp_single_top << rectangle(size=(5,2), layer=pdk.get_glayer("met3"), centered=True)
    minusi_pin.movex(opamp_single_top.xmin).movey(opamp_single_top.ports["diffpair_MINUSgateroute_W_con_N"].center[1])
    iport_antenna1 = movex(minusi_pin.ports["e3"], destination=opamp_single_top.ports["diffpair_MINUSgateroute_W_con_N"].center[0]-9*_max_metal_seperation_ps)
    opamp_single_top << L_route(pdk, opamp_single_top.ports["diffpair_MINUSgateroute_W_con_N"], iport_antenna1)
    iport_antenna2 = movex(iport_antenna1, offsetx=-9*_max_metal_seperation_ps)
    opamp_single_top << straight_route(pdk, iport_antenna1, iport_antenna2, glayer1="met4", glayer2="met4", via2_alignment=('c','c'), via1_alignment=('c','c'), fullbottom=True)
    iport_antenna2.layer = pdk.get_glayer("met4")
    opamp_single_top << straight_route(pdk, iport_antenna2, minusi_pin.ports["e3"], glayer1="met3", via2_alignment=('c','c'), via1_alignment=('c','c'), fullbottom=True)

    # (VIN+)
    plusi_pin = opamp_single_top << rectangle(size=(5,2), layer=pdk.get_glayer("met3"), centered=True)
    plusi_pin.movex(opamp_single_top.xmin + plusi_pin.xmax).movey(opamp_single_top.ports["diffpair_PLUSgateroute_E_con_N"].center[1])
    iport_antenna1_p = movex(plusi_pin.ports["e3"], destination=opamp_single_top.ports["diffpair_PLUSgateroute_E_con_N"].center[0]-9*_max_metal_seperation_ps)
    opamp_single_top << L_route(pdk, opamp_single_top.ports["diffpair_PLUSgateroute_E_con_N"], iport_antenna1_p)
    iport_antenna2_p = movex(iport_antenna1_p, offsetx=-9*_max_metal_seperation_ps)
    opamp_single_top << straight_route(pdk, iport_antenna1_p, iport_antenna2_p, glayer1="met4", glayer2="met4", via2_alignment=('c','c'), via1_alignment=('c','c'), fullbottom=True)
    iport_antenna2_p.layer = pdk.get_glayer("met4")
    opamp_single_top << straight_route(pdk, iport_antenna2_p, plusi_pin.ports["e3"], glayer1="met3", via2_alignment=('c','c'), via1_alignment=('c','c'), fullbottom=True)

    # ==========================================
    # connect diff pair outputs to PMOS load gates
    # ==========================================
    # Left side: Connect the left drain of the diff pair to the gate/drain of the left PMOS load (Diode connection)
    opamp_single_top << straight_route(pdk, opamp_single_top.ports["diffpair_tl_multiplier_0_drain_N"], opamp_single_top.ports["pcomps_minusvia_top_met_S"], glayer1="met5", width=3*pdk.get_grule("met5")["min_width"], via1_alignment_layer="met2", via1_alignment=('c','c'))
    
    # Right side: Connect the right drain of the diff pair to the right PMOS drain
    out_route = opamp_single_top << straight_route(pdk, opamp_single_top.ports["diffpair_tr_multiplier_0_drain_N"], opamp_single_top.ports["pcomps_mimcap_connection_con_S"], glayer1="met5", width=3*pdk.get_grule("met5")["min_width"], via1_alignment_layer="met2", via1_alignment=('c','c'))

    # ==========================================
    # generate and route output pin from the right PMOS load
    # ==========================================
    vout_pin = opamp_single_top << rectangle(size=(5,3), layer=pdk.get_glayer("met4"), centered=True)
    
    
    try:
        vout_port = opamp_single_top.ports["pcomps_mimcap_connection_con_E"]
        vout_pin.movex(opamp_single_top.xmax).movey(vout_port.center[1])
        opamp_single_top << straight_route(pdk, vout_port, vout_pin.ports["e3"], glayer1="met4")
    except KeyError:
        # if the expected port name doesn't exist, default to using the S of the right PMOS load and move the pin accordingly
        vout_port = opamp_single_top.ports["pcomps_mimcap_connection_con_S"]
        vout_pin.movex(opamp_single_top.xmax + 5).movey(vout_port.center[1] - 5)
        opamp_single_top << L_route(pdk, vout_port, vout_pin.ports["e3"], vglayer="met4", hglayer="met4")

    # ==========================================
    # add ports for external connections
    # ==========================================
    opamp_single_top.add_ports(vddpin.get_ports_list(), prefix="pin_vdd_")
    opamp_single_top.add_ports(vbias1.get_ports_list(), prefix="pin_diffpairibias_")
    opamp_single_top.add_ports(minusi_pin.get_ports_list(), prefix="pin_minus_")
    opamp_single_top.add_ports(plusi_pin.get_ports_list(), prefix="pin_plus_")
    opamp_single_top.add_ports(vout_pin.get_ports_list(), prefix="pin_vout_")

    return opamp_single_top, out_route

def opamp_singlestage_netlist(nmos_input_netlist: Netlist, pmos_load_netlist: Netlist) -> Netlist:
    single_stage_netlist = Netlist(
        circuit_name="OPAMP_SINGLE_STAGE",
        nodes=['VDD', 'GND', 'DIFFPAIR_BIAS', 'VP', 'VN', 'VOUT']
    )

    nmos_stage_ref = single_stage_netlist.connect_netlist(
        nmos_input_netlist,
        [
            ('IBIAS', 'DIFFPAIR_BIAS'), 
            ('VSS', 'GND'), 
            ('B', 'GND'),
            ('VDD2', 'VOUT') 
        ]
    )

    pmos_load_ref = single_stage_netlist.connect_netlist(
        pmos_load_netlist,
        [     
            ('VSS', 'VDD'),
            ('VOUT', 'VOUT')        
        ]
    )

    single_stage_netlist.connect_subnets(
        nmos_stage_ref,
        pmos_load_ref,
        [('VDD1', 'VIN')]
    )

    return single_stage_netlist

def opamp_singlestage(
    pdk: MappedPDK,
    half_diffpair_params: tuple[float, float, int] = (6, 1, 4),
    diffpair_bias: tuple[float, float, int] = (6, 2, 4),
    half_pload: tuple[float,float,int] = (6,1,6),
    rmult: int = 2,
    with_antenna_diode_on_diffinputs: int=5
) -> Component:
    """
    Creates a single-stage Operational Transconductance Amplifier (OTA).
    
    Args:
    pdk: MappedPDK to use
    half_diffpair_params: diffpair NMOS dimensions (width, length, fingers)
    diffpair_bias: bias transistor for diffpair NMOS (width, length, fingers)
    half_pload: PMOS active load dimensions (width, length, fingers)
    rmult: routing multiplier (larger = wider routes)
    with_antenna_diode_on_diffinputs: adds antenna diodes on the input gates
    """
    if with_antenna_diode_on_diffinputs != 0 and with_antenna_diode_on_diffinputs < 2:
        raise ValueError("number of antenna diodes should be at least 2 (or 0 to specify no diodes)")

    opamp_single_top, halfmultn_drain_routeref, halfmultn_gate_routeref, _cref = diff_pair_stackedcmirror(
        pdk, 
        half_diffpair_params, 
        diffpair_bias, 
        (1, 1, 2, 2), # Dummy parameter to satisfy the underlying generator
        rmult, 
        with_antenna_diode_on_diffinputs
    )

    opamp_single_top.info['netlist'].circuit_name = "INPUT_STAGE"

    pmos_comps = differential_to_single_ended_converter(pdk, rmult, half_pload, opamp_single_top.ports["diffpair_tl_multiplier_0_drain_N"].center[0])
    clear_cache()

    ydim_ncomps = opamp_single_top.ymax
    pmos_comps_ref = opamp_single_top << pmos_comps
    pmos_comps_ref.movey(round(ydim_ncomps + pmos_comps_ref.ymax+10))
    opamp_single_top.add_ports(pmos_comps_ref.get_ports_list(), prefix="pcomps_")

    clear_cache()
    opamp_single_top, n_to_p_output_route = __create_and_route_pins(pdk, opamp_single_top, pmos_comps_ref)

    opamp_single_top.add_ports(n_to_p_output_route.get_ports_list(), prefix="route_out_")
    opamp_single_top.add_ports(_cref.get_ports_list(), prefix="gnd_route_")

    opamp_single_top.info['netlist'] = opamp_singlestage_netlist(
        opamp_single_top.info['netlist'], 
        pmos_comps.info['netlist']        
    )

    return opamp_single_top

# if __name__ == "__main__":

#     from glayout.flow.pdk.sky130_mapped import sky130_mapped_pdk
#     import os
    
#     print("Generating single-stage opamp layout...")
    

#     my_single_opamp = opamp_singlestage(
#         pdk=sky130_mapped_pdk,
#         half_diffpair_params=(20.7, 1, 10),
#         diffpair_bias=(5, 1, 1),
#         half_pload=(7, 1, 1)
#     )
    

#     gds_filename = "opamp_singlestage.gds"
#     my_single_opamp.write_gds(gds_filename)
#     print(f"Layout successfully exported to: {os.path.abspath(gds_filename)}")
        

#     print("\n================ Extracted SPICE Netlist ================")

#     try:
#         netlist_str = my_single_opamp.info['netlist'].generate_netlist()
#         print(netlist_str)
#     except AttributeError:
#         print(my_single_opamp.info['netlist'])
