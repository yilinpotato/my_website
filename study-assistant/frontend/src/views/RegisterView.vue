<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const form = reactive({
  email: '',
  username: '',
  password: '',
  role: 'student',
  display_name: '',
})

const message = ref('')
const error = ref('')

const handleSubmit = async () => {
  message.value = ''
  error.value = ''
  try {
    await authStore.register(form)
    message.value = '注册成功，验证码已发送至邮箱，请尽快验证。'
    setTimeout(() => router.push('/verify'), 1200)
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <section class="auth-card">
    <header>
      <p class="eyebrow">Create account</p>
      <h1>注册 AI 学习助手</h1>
      <p>使用邮箱注册，系统会发送 6 位验证码，验证后即可登录。</p>
    </header>

    <form @submit.prevent="handleSubmit">
      <label>
        邮箱
        <input v-model="form.email" type="email" placeholder="name@example.com" required />
      </label>
      <label>
        账户名
        <input
          v-model="form.username"
          type="text"
          placeholder="3-20 位字母或数字"
          required
          minlength="3"
          maxlength="20"
          pattern="^[A-Za-z0-9_.\-]{3,20}$"
        />
      </label>
      <label>
        密码
        <input v-model="form.password" type="password" placeholder="至少 8 位" minlength="8" required />
      </label>
      <label>
        角色
        <select v-model="form.role">
          <option value="student">学生</option>
          <option value="parent">家长</option>
        </select>
      </label>
      <label>
        昵称（可选）
        <input v-model="form.display_name" type="text" placeholder="不填则自动生成" />
      </label>
      <button class="primary" type="submit">提交注册</button>
    </form>

    <div class="actions">
      <RouterLink to="/login">已有账号？前往登录</RouterLink>
    </div>

    <p v-if="message" class="status success">{{ message }}</p>
    <p v-if="error" class="status error">{{ error }}</p>
  </section>
</template>

<style scoped>
.auth-card {
  max-width: 520px;
  margin: 2rem auto;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
}

h1 {
  font-size: 2rem;
  color: #0f1d40;
}

form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

label {
  display: flex;
  flex-direction: column;
  font-weight: 600;
  color: #112347;
  gap: 0.4rem;
}

input,
select {
  border-radius: 999px;
  border: 1px solid rgba(17, 62, 125, 0.25);
  padding: 0.8rem 1.2rem;
}

.primary {
  border: none;
  border-radius: 999px;
  padding: 0.9rem 1.8rem;
  font-weight: 600;
  background: linear-gradient(120deg, #6f9dec, #8bd4c8);
  color: #fff;
  cursor: pointer;
}

.actions a {
  color: #0b2a6b;
}

.status {
  font-weight: 600;
}

.status.success {
  color: #0a8060;
}

.status.error {
  color: #c44545;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.82rem;
  margin-bottom: 0.4rem;
}
</style>
