<script setup>
/**
 * Переиспользуемое модальное окно подтверждения.
 * Пропсы:
 *   open     — показывать ли модалку (управляется родителем);
 *   title    — заголовок;
 *   message  — необязательный текст под заголовком;
 *   buttons  — массив кнопок: [{ label, variant?, onClick? }],
 *              где variant ∈ 'primary'|'secondary'|'danger', onClick — обработчик.
 * Также поддерживает слот по умолчанию для произвольного содержимого.
 * Эмитит `close` (Esc, клик по фону, после нажатия любой кнопки).
 */
import { watch, onBeforeUnmount } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  message: { type: String, default: '' },
  buttons: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

function close() {
  emit('close')
}

function onButton(b) {
  if (b && typeof b.onClick === 'function') b.onClick()
  close()
}

function onKey(e) {
  if (e.key === 'Escape' && props.open) close()
}

watch(
  () => props.open,
  (v) => {
    if (v) document.addEventListener('keydown', onKey)
    else document.removeEventListener('keydown', onKey)
  },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <Transition name="modal">
    <div v-if="open" class="modal-backdrop" @click.self="close">
      <div class="modal-card" role="dialog" aria-modal="true">
        <h3 v-if="title" class="modal-title">{{ title }}</h3>
        <p v-if="message" class="modal-message">{{ message }}</p>
        <slot />
        <div class="modal-actions">
          <button
            v-for="(b, i) in buttons"
            :key="i"
            type="button"
            :class="['btn', b.variant && b.variant !== 'primary' ? b.variant : '']"
            @click="onButton(b)"
          >
            {{ b.label }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>
