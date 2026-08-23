from app import app, db, User  # 从您的主应用导入 app, db 和 User 模型
from werkzeug.security import generate_password_hash

def reset_all_user_passwords(new_password='666666'):
    with app.app_context():
        try:
            users = User.query.all()
            if not users:
                print("数据库中没有用户。")
                return

            for user in users:
                # 使用新的哈希方法为每个用户设置新密码
                user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256:260000')
                print(f"正在重置用户 '{user.username}' 的密码...")
            
            db.session.commit()
            print("\n所有用户的密码已成功重置为:", new_password)

        except Exception as e:
            db.session.rollback()
            print(f"发生错误: {e}")

if __name__ == '__main__':
    reset_all_user_passwords()