#!/usr/bin/env python3
import subprocess
import argparse
import os
import tempfile

def create_hybrid_file(original_file, carrier_image, output_file):
    """
    将一个文件打包成RAR，并附加到一个图片文件后面。
    """
    if not os.path.exists(original_file):
        print(f"错误: 原始文件 '{original_file}' 不存在。")
        return False

    if not os.path.exists(carrier_image):
        print(f"错误: 载体图片 '{carrier_image}' 不存在。")
        return False

    # 使用临时文件来存放RAR压缩包，避免产生垃圾文件
    with tempfile.NamedTemporaryFile(suffix='.rar', delete=False) as tmp_rar:
        temp_rar_path = tmp_rar.name

    try:
        # 1. 将原始文件压缩成RAR
        # rar a [选项] <压缩文件名> <要压缩的文件>
        # -ep: 忽略路径，只包含文件名
        print(f"正在将 '{original_file}' 压缩到 '{temp_rar_path}'...")
        # 注意：rar命令的语法，压缩文件名在前，源文件在后
        rar_process = subprocess.run(
            ['rar', 'a', '-ep', temp_rar_path, original_file],
            capture_output=True, text=True
        )
        if rar_process.returncode != 0:
            print("错误: RAR压缩失败。")
            print(rar_process.stderr)
            return False

        # 2. 拼接图片和RAR文件 (核心步骤)
        # 在Linux/macOS下，使用 'cat' 命令进行二进制拼接
        print(f"正在将 '{carrier_image}' 和 '{temp_rar_path}' 拼接成 '{output_file}'...")
        with open(output_file, 'wb') as f_out:
            with open(carrier_image, 'rb') as f_img:
                f_out.write(f_img.read())
            with open(temp_rar_path, 'rb') as f_rar:
                f_out.write(f_rar.read())
        
        print("成功！")
        print(f" - 文件 '{output_file}' 已创建。")
        print(f" - 它可以作为图片直接查看。")
        print(f" - 将其后缀改为 .rar 后，可以用解压软件打开得到 '{os.path.basename(original_file)}'。")
        return True

    finally:
        # 3. 清理临时文件
        if os.path.exists(temp_rar_path):
            os.remove(temp_rar_path)
            print(f"临时文件 '{temp_rar_path}' 已删除。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="创建一个图片和RAR压缩包的混合文件。")
    parser.add_argument('-f', '--file', required=True, help="要隐藏在压缩包中的原始文件。")
    parser.add_argument('-i', '--image', required=True, help="用作载体的图片文件（例如 a.jpg）。")
    parser.add_argument('-o', '--output', required=True, help="最终输出的混合文件名（例如 a_hybrid.jpg）。")
    
    args = parser.parse_args()
    
    create_hybrid_file(args.file, args.image, args.output)