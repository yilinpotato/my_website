# generate_hash.py
from werkzeug.security import generate_password_hash
import getpass

# 使用getpass可以安全地输入密码，不会显示在屏幕上
password = getpass.getpass("请输入您要设置的管理员密码: ")
password_confirm = getpass.getpass("请再次输入以确认: ")

if password != password_confirm:
    print("两次输入的密码不一致！")
else:
    # 生成哈希值，method='pbkdf2:sha256'是Flask默认的安全方法
    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
    print("\n========================================================")
    print("您的密码哈希值已生成！请将其复制到 app.py 中。")
    print("========================================================")
    print(hashed_password)
    print("\n")
    