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

# 导入 Sky130 PDK
from glayout.flow.pdk.sky130_mapped import sky130_mapped_pdk as pdk

# 尝试导入我们刚写的 ldo 生成函数
try:
    from glayout.flow.blocks.composite.ldo.ldo import ldo
except ImportError:
    print("错误: 找不到 ldo.py，请确保 ldo.py 和本脚本在同一目录下！")
    sys.exit(1)

if __name__ == "__main__":
    start_watch = time.time()

    parser = argparse.ArgumentParser(description="Sky130 LDO Layout Generation Utility")
    subparsers = parser.add_subparsers(title="mode", required=True, dest="mode")

    # 定义 gen_ldo 模式
    gen_ldo_parser = subparsers.add_parser("gen_ldo", help="Run the LDO layout generation function.")
    
    # 根据 SPICE 网表的默认值定义参数接口
    gen_ldo_parser.add_argument("--half_diffpair_params", nargs=3, type=float, default=[20.7, 1.0, 10], help="差分对参数: W, L, Fingers")
    gen_ldo_parser.add_argument("--diffpair_bias", nargs=3, type=float, default=[5.0, 1.0, 1], help="差分对偏置参数: W, L, Fingers")
    gen_ldo_parser.add_argument("--half_pload", nargs=3, type=float, default=[7.0, 1.0, 1], help="电流镜负载参数: W, L, Fingers")
    
    # 功率管参数 (W, L, Fingers, Multipliers)
    # 默认值 21.818, 0.15, 50, 20 (等效原网表的 m=1000)
    gen_ldo_parser.add_argument("--pass_transistor_params", nargs=4, type=float, default=[21.818, 0.15, 50, 20], help="功率管参数: W, L, Fingers, Multipliers")
    
    gen_ldo_parser.add_argument("--rmult", type=int, default=3, help="金属布线倍乘系数 (增加以承载更大电流)")
    gen_ldo_parser.add_argument("--output_gds", type=str, default="sky130_ldo_output.gds", help="输出的 GDS 文件名")
    gen_ldo_parser.add_argument("--no_show", action="store_true", help="如果设置，生成后不自动弹出预览窗口")

    args = parser.parse_args()

    if args.mode == "gen_ldo":
        print("====== 开始生成 LDO 版图 ======")
        print(f"PDK: Sky130")
        print(f"功率管配置: W={args.pass_transistor_params[0]}um, Fingers={int(args.pass_transistor_params[2])}, Multipliers={int(args.pass_transistor_params[3])}")
        
        # 将参数从 list 转为 tuple 传给 ldo 函数
        ldo_comp = ldo(
            pdk=pdk,
            half_diffpair_params=tuple(args.half_diffpair_params),
            diffpair_bias=tuple(args.diffpair_bias),
            half_pload=tuple(args.half_pload),
            pass_transistor_params=tuple(args.pass_transistor_params),
            rmult=args.rmult
        )
        
        # 弹出窗口展示
        if not args.no_show:
            print("\n正在启动 KLayout 预览...")
            ldo_comp.show()
            
        # 写入物理文件
        if args.output_gds:
            output_path = Path(args.output_gds).resolve()
            ldo_comp.write_gds(output_path)
            print(f"✅ GDS 版图已成功保存至: {output_path}")

    end_watch = time.time()
    print(f"运行总耗时: {round(end_watch - start_watch, 2)} 秒")