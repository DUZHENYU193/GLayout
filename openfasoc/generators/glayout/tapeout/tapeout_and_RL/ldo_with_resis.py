import sys
import time
from os import path, rename, environ
environ['OPENBLAS_NUM_THREADS'] = '1'
from pathlib import Path
# path to glayout
sys.path.append(path.join(str(Path(__file__).resolve().parents[2])))

from gdsfactory.cell import cell
from gdsfactory.component import Component
from gdsfactory.components import rectangle, text_freetype
from glayout.flow.pdk.mappedpdk import MappedPDK
from glayout.flow.pdk.sky130_mapped import sky130_mapped_pdk as pdk

# 导入 glayout 的物理器件
from glayout.flow.placement.two_transistor_interdigitized import two_pfet_interdigitized, two_nfet_interdigitized
from glayout.flow.primitives.fet import pmos
from glayout.flow.primitives.mimcap import mimcap_array
from glayout.flow.pdk.util.comp_utils import evaluate_bbox, align_comp_to_port
from glayout.flow.pdk.util.snap_to_grid import component_snap_to_grid

@cell
def practical_ldo_macro(pdk: MappedPDK) -> Component:
    """
    实用的片外补偿型 LDO 版图 (无片上负载大电容)。
    包含：有源 OTA 核心、巨型功率管、左侧 RC 补偿网络、右侧分压电阻区域。
    """
    ldo_top = Component(name="PRACTICAL_LDO_MACRO")
    metal_sep = pdk.util_max_metal_seperation()

    print("正在生成有源器件区 (Active Core)...")
    # =====================================================================
    # 1. 有源器件：OTA 核心 与 功率管
    # =====================================================================
    pmos_load = ldo_top << two_pfet_interdigitized(pdk, width=7.0, length=1.0, numcols=2, dummy=True, with_substrate_tap=True, tie_layers=("met2", "met2"))
    nmos_dp = ldo_top << two_nfet_interdigitized(pdk, width=207.0, length=1.0, numcols=20, dummy=True)
    nmos_tail = ldo_top << two_nfet_interdigitized(pdk, width=5.0, length=1.0, numcols=2, dummy=True, with_substrate_tap=True, tie_layers=("met2", "met2"))
    
    # 巨型功率管 (50 fingers * 20 multipliers)
    pass_fet = ldo_top << pmos(pdk, width=21.818, length=0.15, fingers=50, multipliers=20, sd_route_topmet="met4", gate_route_topmet="met3", rmult=4, with_tie=True, tie_layers=("met2", "met2"))

    # 有源区垂直堆叠对齐
    nmos_tail.move([0, 0])
    nmos_dp.movey(nmos_tail.ymax + metal_sep * 5)
    pmos_load.movey(nmos_dp.ymax + metal_sep * 5)
    pass_fet.movey(pmos_load.ymax + metal_sep * 15) # 功率管稍微拉近一点
    
    # 居中对齐有源器件
    center_x = pass_fet.center[0]
    nmos_tail.movex(center_x - nmos_tail.center[0])
    nmos_dp.movex(center_x - nmos_dp.center[0])
    pmos_load.movex(center_x - pmos_load.center[0])

    print("正在生成无源器件区 (Passives Area)...")
    # =====================================================================
    # 2. 无源器件：补偿电容与电阻 (C1, R1, R2, R3)
    # =====================================================================
    
    # [C1] 补偿电容 30pF -> 8x8 MIM 阵列 (面积适中，可放在片内)
    c1_comp = ldo_top << mimcap_array(pdk, rows=8, columns=8)
    
    # [R1] 补偿电阻 20Ω 
    r1_box = ldo_top << rectangle(size=(10, 5), layer=pdk.get_glayer("met3"), centered=True)
    r1_label = ldo_top << text_freetype("R1_20_Ohm", size=2, layer=pdk.get_glayer("met4"))
    
    # [R2 & R3] 500kΩ 分压反馈电阻
    # 设置两个 80x80um 的宏单元区域留给高阻值多晶硅电阻
    r2_box = ldo_top << rectangle(size=(80, 80), layer=pdk.get_glayer("met3"), centered=True)
    r2_label = ldo_top << text_freetype("R2_500k_Ohm", size=5, layer=pdk.get_glayer("met4"))
    
    r3_box = ldo_top << rectangle(size=(80, 80), layer=pdk.get_glayer("met3"), centered=True)
    r3_label = ldo_top << text_freetype("R3_500k_Ohm", size=5, layer=pdk.get_glayer("met4"))

    # =====================================================================
    # 3. 全局版图规划 (Global Floorplanning)
    # 布局策略：中心是有源区，左侧是 RC 补偿，右侧是反馈分压
    # =====================================================================
    
    # C1 (30pF) 放在 OTA 左侧，与差分对底部对齐
    c1_comp.movex(nmos_dp.xmin - c1_comp.xmax - metal_sep * 20)
    c1_comp.movey(nmos_dp.ymin)

    # R1 (20Ω) 放在 C1 的右上方，靠近功率管栅极，方便后续走线
    r1_box.move([c1_comp.xmax + 15, c1_comp.ymax + 10])
    align_comp_to_port(r1_label, r1_box.ports["e1"])

    # R2 和 R3 (各500kΩ) 放在有源区的右侧，上下堆叠
    r2_box.move([pass_fet.xmax + metal_sep * 20 + 40, nmos_dp.center[1] + 45])
    align_comp_to_port(r2_label, r2_box.ports["e1"])
    
    r3_box.move([pass_fet.xmax + metal_sep * 20 + 40, nmos_dp.center[1] - 45])
    align_comp_to_port(r3_label, r3_box.ports["e1"])

    return component_snap_to_grid(ldo_top)

if __name__ == "__main__":
    print("====== 开始生成实用型 LDO 版图 ======")
    start_watch = time.time()
    
    my_ldo = practical_ldo_macro(pdk=pdk)
    
    output_filename = "sky130_resis_ldo.gds"
    my_ldo.write_gds(output_filename)
    
    end_watch = time.time()
    print(f"✅ 生成成功！耗时: {round(end_watch - start_watch, 2)} 秒")
    print("正在启动 KLayout 预览...")
    my_ldo.show()