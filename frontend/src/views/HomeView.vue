<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client'

const sources = ref([])
const loading = ref(true)
const error = ref('')
const q = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    sources.value = await api.sources({ q: q.value.trim() })
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1>Источники базы знаний</h1>
    <div class="filters">
      <input v-model="q" placeholder="Поиск по названию…" @keyup.enter="load" />
      <button class="btn secondary" @click="load">Применить</button>
    </div>

    <div v-if="loading" class="state">Загрузка…</div>
    <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
    <div v-else-if="!sources.length" class="state">Ничего не найдено.</div>

    <template v-else>
      <div class="muted" style="margin:8px 0">Всего источников: {{ sources.length }}</div>
      <div class="grid">
        <router-link
          v-for="s in sources"
          :key="s.id"
          class="card"
          :to="{ name: 'reader', params: { id: s.id } }"
        >
          <h3>{{ s.title }}</h3>
          <div class="meta">
            <span>{{ s.num_pages }} стр.</span>
            <span v-if="s.has_pdf"> · PDF</span>
          </div>
        </router-link>
      </div>
    </template>
  </div>
</template>
