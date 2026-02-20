import sys
import argparse
import time
from pathlib import Path
import os
from os import path, rename, environ
environ['OPENBLAS_NUM_THREADS'] = '1'
from pathlib import Path
# path to glayout
sys.path.append(path.join(str(Path(__file__).resolve().parents[2])))

# 屏蔽 OpenBLAS 多线程干扰
os.environ['OPENBLAS_NUM_THREADS'] = '1'

from gdsfactory.cell import cell
from gdsfactory.component import Component
from glayout.flow.pdk.mappedpdk import MappedPDK
from glayout.flow.pdk.sky130_mapped import sky130_mapped_pdk as pdk

# 导入 glayout 的基础物理器件
from glayout.flow.placement.two_transistor_interdigitized import two_pfet_interdigitized, two_nfet_interdigitized
from glayout.flow.primitives.fet import pmos
from glayout.flow.pdk.util.comp_utils import evaluate_bbox
from glayout.flow.pdk.util.snap_to_grid import component_snap_to_grid

@cell
def custom_single_stage_ldo(pdk: MappedPDK) -> Component:
    """
    完全根据 Regulator.spice 手工搭建的单级 OTA LDO 版图。
    包含：电流镜负载(PMOS)、差分对(NMOS)、尾电流源(NMOS) 和 功率管(PMOS)。
    """
    ldo_top = Component(name="CUSTOM_5T_LDO")
    metal_sep = pdk.util_max_metal_seperation()

    # =====================================================================
    # 1. 实例化网表中的器件 (Device Instantiation)
    # =====================================================================
    
    # [1] PMOS 电流镜负载 (XM36, XM1)
    # SPICE: W=7, L=1, m=1
    # 我们使用 2 个 finger 来优化版图比例
    pmos_load = ldo_top << two_pfet_interdigitized(
        pdk, 
        width=7.0, 
        length=1.0, 
        numcols=2, 
        dummy=True,
        with_substrate_tap=True,
        tie_layers=("met2", "met2")
    )

    # [2] NMOS 差分对 (XM31, XM2)
    # SPICE: W=20.7, L=1, m=10 -> 总宽度 207um！
    # 这是一个极大的差分对，为了版图美观，我们使用 20 个 finger，每个约 10.35um
    nmos_dp = ldo_top << two_nfet_interdigitized(
        pdk, 
        width=207.0, 
        length=1.0, 
        numcols=20, 
        dummy=True,
        with_substrate_tap=False # 衬底在外部统一打孔
    )

    # [3] NMOS 尾电流源与偏置 (XM34, XM3)
    # SPICE: W=5, L=1, m=1
    nmos_tail = ldo_top << two_nfet_interdigitized(
        pdk, 
        width=5.0, 
        length=1.0, 
        numcols=2, 
        dummy=True,
        with_substrate_tap=True,
        tie_layers=("met2", "met2")
    )

    # [4] 巨型 PMOS 功率调整管 (XMpass)
    # SPICE: W=21.818, L=0.15, m=1000 -> 总宽度 ~21818um
    # 我们折算为 50 fingers * 20 multipliers 阵列
    pass_fet = ldo_top << pmos(
        pdk,
        width=21.818,
        length=0.15,
        fingers=50,
        multipliers=20,
        sd_route_topmet="met4", 
        gate_route_topmet="met3",
        rmult=4, # 加粗走线应对大电流
        with_tie=True,
        tie_layers=("met2", "met2")
    )

    # =====================================================================
    # 2. 物理布局规划 (Floorplanning / Placement)
    # 策略：从下到上垂直堆叠 (尾电流 -> 差分对 -> PMOS负载 -> 功率管)
    # =====================================================================
    
    # 放置尾电流源作为原点基准
    nmos_tail.move([0, 0])

    # 放置差分对 (在尾电流源上方)
    tail_dims = evaluate_bbox(nmos_tail)
    nmos_dp.movey(nmos_tail.ymax + metal_sep * 5)
    
    # 放置 PMOS 负载 (在差分对上方)
    dp_dims = evaluate_bbox(nmos_dp)
    pmos_load.movey(nmos_dp.ymax + metal_sep * 5)

    # 放置功率管 (在整个 OTA 上方，预留较大空间)
    load_dims = evaluate_bbox(pmos_load)
    pass_fet.movey(pmos_load.ymax + metal_sep * 20)

    # 将所有模块在 X 轴上居中对齐
    center_x = pass_fet.center[0]
    nmos_tail.movex(center_x - nmos_tail.center[0])
    nmos_dp.movex(center_x - nmos_dp.center[0])
    pmos_load.movex(center_x - pmos_load.center[0])

    # =====================================================================
    # 3. 端口引出与验证标注 (Ports & Labels)
    # =====================================================================
    ldo_top.add_ports(pass_fet.get_ports_list(), prefix="PT_")
    ldo_top.add_ports(pmos_load.get_ports_list(), prefix="LOAD_")
    ldo_top.add_ports(nmos_dp.get_ports_list(), prefix="DP_")
    ldo_top.add_ports(nmos_tail.get_ports_list(), prefix="TAIL_")

    return component_snap_to_grid(ldo_top)

if __name__ == "__main__":
    print("====== 开始生成单级 5T OTA LDO 版图 ======")
    start_watch = time.time()
    
    # 生成版图
    my_ldo = custom_single_stage_ldo(pdk=pdk)
    
    # 导出 GDS
    output_filename = "sky130_custom_ldo.gds"
    my_ldo.write_gds(output_filename)
    
    end_watch = time.time()
    print(f"✅ 生成成功！耗时: {round(end_watch - start_watch, 2)} 秒")
    print(f"📁 文件已保存至: {Path(output_filename).resolve()}")
    
    # 自动弹出 KLayout 预览
    print("正在启动 KLayout 预览...")
    my_ldo.show()