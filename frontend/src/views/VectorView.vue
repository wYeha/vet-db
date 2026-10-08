<script setup>
// Векторный поиск — статичная заглушка «скоро». Никаких вызовов бэкенда и модели:
// семантический поиск по эмбеддингам ещё не реализован, бюджет не тратим.
</script>

<template>
  <div class="vector">
    <h1>Векторный поиск</h1>
    <div class="state">
      <p><strong>Скоро.</strong> Семантический поиск по эмбеддингам ещё в разработке.</p>
      <p class="muted">
        Пока пользуйтесь
        <router-link :to="{ name: 'search' }">полнотекстовым поиском</router-link>
        или
        <router-link :to="{ name: 'ontology' }">поиском по онтологии</router-link>.
      </p>
    </div>
  </div>
</template>
