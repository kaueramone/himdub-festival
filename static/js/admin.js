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

// Keep password visibility local to each field; never read or copy its value.
const eyeIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="password-eye-slash" d="m3 3 18 18"/></svg>';
document.querySelectorAll('input[type="password"]').forEach((input, index) => {
  // An implicit label may also contain the toggle. Keep the field's own
  // accessible name stable when the button changes between show and hide.
  if (!input.hasAttribute('aria-label') && !input.hasAttribute('aria-labelledby') && input.labels?.length) {
    input.setAttribute('aria-label', input.labels[0].textContent.trim().replace(/\s+/g, ' '));
  }
  const wrapper = document.createElement('span');
  wrapper.className = 'password-field';
  input.before(wrapper);
  wrapper.append(input);
  if (!input.id) input.id = `password-field-${index}`;
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'password-toggle';
  toggle.innerHTML = eyeIcon;
  toggle.setAttribute('aria-controls', input.id);
  const setVisible = (visible) => {
    input.type = visible ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', String(visible));
    toggle.setAttribute('aria-label', visible ? 'Ocultar palavra-passe' : 'Mostrar palavra-passe');
    toggle.title = visible ? 'Ocultar palavra-passe' : 'Mostrar palavra-passe';
  };
  setVisible(false);
  toggle.addEventListener('click', () => setVisible(input.type === 'password'));
  input.form?.addEventListener('submit', () => setVisible(false));
  window.addEventListener('pagehide', () => setVisible(false));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) setVisible(false);
  });
  wrapper.append(toggle);
});
