<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client'

const items = ref([])
const loading = ref(true)
const error = ref('')
const species = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = await api.diseases({ species: species.value })
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
    <h1>Диагностика — болезни</h1>
    <div class="filters">
      <select v-model="species" @change="load">
        <option value="">Все виды</option>
        <option value="avian">Птица</option>
        <option value="swine">Свиньи</option>
      </select>
    </div>

    <div v-if="loading" class="state">Загрузка…</div>
    <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
    <div v-else-if="!items.length" class="state">Ничего не найдено.</div>

    <div v-else class="grid">
      <router-link
        v-for="d in items"
        :key="d.id"
        class="card"
        :to="{ name: 'disease', params: { id: d.id } }"
      >
        <h3>{{ d.name }}</h3>
        <div class="meta"><span class="badge">{{ d.species === 'avian' ? 'Птица' : 'Свиньи' }}</span></div>
      </router-link>
    </div>
  </div>
</template>
