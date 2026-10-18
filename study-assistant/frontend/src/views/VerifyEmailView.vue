<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const email = ref('')
const code = ref('')
const message = ref('')
const error = ref('')

const handleVerify = async () => {
  message.value = ''
  error.value = ''
  try {
    await authStore.verifyEmail({ email: email.value, code: code.value })
    message.value = '邮箱验证成功，正在跳转到个人中心...'
    setTimeout(() => router.push('/profile'), 800)
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <section class="auth-card">
    <header>
      <p class="eyebrow">Email verification</p>
      <h1>验证注册邮箱</h1>
      <p>请输入注册邮箱和收到的 6 位验证码，完成后即可登录和完善资料。</p>
    </header>

    <form @submit.prevent="handleVerify">
      <label>
        邮箱
        <input v-model="email" type="email" placeholder="name@example.com" required />
      </label>
      <label>
        验证码
        <input v-model="code" type="text" placeholder="输入 6 位验证码" minlength="6" maxlength="6" required />
      </label>
      <button class="primary" type="submit">完成验证</button>
    </form>

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

input {
  border-radius: 999px;
  border: 1px solid rgba(17, 62, 125, 0.25);
  padding: 0.8rem 1.2rem;
  font-size: 1rem;
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
