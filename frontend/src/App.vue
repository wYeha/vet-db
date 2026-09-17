<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from './api/client'

const router = useRouter()
const q = ref('')
const llmConfigured = ref(false)

onMounted(async () => {
  try {
    const h = await api.health()
    llmConfigured.value = !!h.llm_configured
  } catch (_) { /* health недоступен — прячем чат */ }
})

function submitSearch() {
  const query = q.value.trim()
  if (query) router.push({ name: 'search', query: { q: query } })
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <router-link to="/" class="brand">VetAI · База знаний</router-link>
      <nav class="nav">
        <router-link to="/">Источники</router-link>
        <router-link to="/pharma">Фарма</router-link>
        <router-link to="/diseases">Диагностика</router-link>
        <router-link
          v-if="llmConfigured"
          to="/chat"
        >Ассистент</router-link>
        <span
          v-else
          class="nav-disabled"
          title="Чат появится после подключения ключа модели"
        >Ассистент</span>
      </nav>
      <form class="global-search" @submit.prevent="submitSearch">
        <input v-model="q" type="search" placeholder="Глобальный поиск…" />
        <button type="submit">Найти</button>
      </form>
    </header>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>
