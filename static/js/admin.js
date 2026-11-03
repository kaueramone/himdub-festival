document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  const input = button.closest('.app-notice').querySelector('.copy-source');
  try { await navigator.clipboard.writeText(input.value); button.textContent='Link copiado'; }
  catch { input.focus(); input.select(); button.textContent='Selecionado — copia o link'; }
});
document.addEventListener('submit', (event) => {
  if (event.target.dataset.confirm && !window.confirm(event.target.dataset.confirm)) event.preventDefault();
});
