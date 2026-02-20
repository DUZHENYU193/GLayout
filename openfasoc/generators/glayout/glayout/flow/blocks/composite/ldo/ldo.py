from gdsfactory.cell import cell
from gdsfactory.component import Component
from gdsfactory.component_reference import ComponentReference
from glayout.flow.pdk.mappedpdk import MappedPDK
from glayout.flow.primitives.fet import pmos
from glayout.flow.routing.L_route import L_route
from glayout.flow.routing.c_route import c_route
from glayout.flow.pdk.util.comp_utils import evaluate_bbox, align_comp_to_port
from glayout.flow.pdk.util.port_utils import rename_ports_by_orientation, set_port_orientation
from glayout.flow.pdk.util.snap_to_grid import component_snap_to_grid
from pydantic import validate_arguments
from glayout.flow.spice import Netlist

# 尝试导入底层的 opamp
try:
    from glayout.flow.blocks.composite.opamp import opamp 
except ImportError:
    print("警告: 无法导入 opamp，请检查 glayout 环境变量设置！")

def ldo_pass_stage_netlist(pdk: MappedPDK, pass_fet_ref: ComponentReference) -> Netlist:
    """功率管的网表"""
    pass_stage_netlist = Netlist(
        circuit_name="PASS_STAGE",
        nodes=['VDD', 'EA_OUT', 'VOUT']
    )
    pass_stage_netlist.connect_netlist(
        pass_fet_ref.info['netlist'],
        [('D', 'VOUT'), ('G', 'EA_OUT'), ('B', 'VDD'), ('S', 'VDD')]
    )
    return pass_stage_netlist

def ldo_netlist(ea_netlist: Netlist, pass_stage_netlist: Netlist) -> Netlist:
    """LDO 闭环顶层网表"""
    top_level_netlist = Netlist(
        circuit_name="ldo",
        nodes=["VDD", "GND", "VREF", "VOUT", "DIFFPAIR_BIAS", "CS_BIAS"]
    )
    # 误差放大器连接：VN接反馈VOUT，VP接参考电压VREF
    top_level_netlist.connect_netlist(
        ea_netlist,
        [
            ('VDD', 'VDD'), 
            ('GND', 'GND'), 
            ('VP', 'VREF'),             
            ('VN', 'VOUT'),             
            ('VOUT', 'EA_OUT'),         
            ('DIFFPAIR_BIAS', 'DIFFPAIR_BIAS'), 
            ('CS_BIAS', 'CS_BIAS')
        ]
    )
    # 功率管连接
    top_level_netlist.connect_netlist(
        pass_stage_netlist,
        [('VDD', 'VDD'), ('EA_OUT', 'EA_OUT'), ('VOUT', 'VOUT')]
    )
    return top_level_netlist

@validate_arguments
def __add_pass_stage(
    pdk: MappedPDK,
    ldo_top: Component,
    passParams: tuple[float, float, int, int], 
    rmult: int,
) -> tuple[Component, Netlist]:
    '''添加巨型功率调整管 XMpass 并完成布线'''
    # 解锁顶层 Component 以允许修改 (修复 MutabilityError)
    ldo_top.unlock()

    # 实例化功率管 (注意：pmos 没有 with_dnwell 参数)
    pass_fet_ref = ldo_top << pmos(
        pdk,
        width=passParams[0],    
        length=passParams[1],   
        fingers=passParams[2],  
        multipliers=passParams[3], 
        sd_route_topmet="met4", 
        gate_route_topmet="met3",
        rmult=rmult,
        with_tie=True,
        tie_layers=("met2","met2")
    )

    metal_sep = pdk.util_max_metal_seperation()

    # 定位：将功率管放在误差放大器的正上方
    ea_dims = evaluate_bbox(ldo_top)
    pt_dims = evaluate_bbox(pass_fet_ref)
    x_cord = ldo_top.center[0]
    y_cord = ldo_top.ymax + pt_dims[1]/2 + metal_sep * 15
    pass_fet_ref.move([x_cord, y_cord])

    # 1. 路由：EA 输出 -> 功率管栅极
    ea_out_ports = [p for p in ldo_top.get_ports_list() if "pin_output" in p.name]
    if ea_out_ports:
        ea_out_port = ea_out_ports[0]
        ldo_top << L_route(pdk, ea_out_port, pass_fet_ref.ports["multiplier_0_gate_S"])

    # 2. 路由：功率管漏极 (VOUT) -> EA 负输入 (闭环反馈)
    ea_minus_ports = [p for p in ldo_top.get_ports_list() if "pin_minus" in p.name]
    if ea_minus_ports:
        ea_minus_port = ea_minus_ports[0]
        # 修复 ValueError: 强行将目标端口的朝向设为与起点一致 (W)
        ldo_top << c_route(
            pdk, 
            pass_fet_ref.ports["multiplier_0_drain_W"], 
            set_port_orientation(ea_minus_port, "W"), 
            extension=metal_sep*12, 
            width1=2
        )

    # 3. 路由：功率管源极 -> VDD (宽线强电流)
    vdd_ports = [p for p in ldo_top.get_ports_list() if "pin_vdd" in p.name]
    if vdd_ports:
        ea_vdd_port = vdd_ports[0]
        ldo_top << c_route(
            pdk, 
            pass_fet_ref.ports["multiplier_0_source_E"], 
            set_port_orientation(ea_vdd_port, "E"), 
            width1=8, width2=8, 
            extension=metal_sep*10
        )

    pass_stage_netlist = ldo_pass_stage_netlist(pdk, pass_fet_ref)
    
    # 导出功率管 VOUT 的 Label
    ldo_top.add_ports(pass_fet_ref.get_ports_list(), prefix="pass_fet_")
    
    return ldo_top, pass_stage_netlist

@cell
def ldo(
    pdk: MappedPDK,
    half_diffpair_params: tuple[float, float, int] = (20.7, 1.0, 10),
    diffpair_bias: tuple[float, float, int] = (5.0, 1.0, 1),
    half_pload: tuple[float, float, int] = (7.0, 1.0, 1),
    pass_transistor_params: tuple[float, float, int, int] = (21.818, 0.15, 50, 20),
    rmult: int = 3,
) -> Component:
    """
    根据 SPICE 网表生成 LDO 版图。
    调用 glayout 的 opamp 作为误差放大器核心，外挂功率管闭环。
    """
    # 构造 Dummy 的共源级（欺骗 opamp 发生器，因为它默认是两级运放）
    dummy_cs_params = (1.0, 0.5, 2, 2)
    dummy_cs_bias = (1.0, 0.5, 2, 2)

    # 1. 实例化误差放大器
    ldo_top = opamp(
        pdk=pdk,
        half_diffpair_params=half_diffpair_params,
        diffpair_bias=diffpair_bias,
        half_pload=half_pload,
        half_common_source_params=dummy_cs_params,
        half_common_source_bias=dummy_cs_bias,
        rmult=rmult,
        add_output_stage=False # 不添加 opamp 的源极跟随器输出
    )

    # 2. 挂载真实的 LDO 功率管级并布线
    ldo_top, pass_stage_netlist = __add_pass_stage(pdk, ldo_top, pass_transistor_params, rmult)

    # 3. 合并网表
    ldo_top.info['netlist'] = ldo_netlist(ldo_top.info['netlist'], pass_stage_netlist)

    return rename_ports_by_orientation(component_snap_to_grid(ldo_top))