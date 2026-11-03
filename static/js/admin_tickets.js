(() => {
  const add = document.getElementById('add-ticket-tier');
  if (!add) return;
  const count = document.getElementById('tier-count');
  add.addEventListener('click', () => {
    const index = Number(count.value);
    if (index >= 20) return;
    const template = document.getElementById('ticket-tier-template');
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll('[name]').forEach(el => { el.name = el.name.replace('__INDEX__', String(index)); });
    fragment.querySelector('[data-tier-number]').textContent = String(index + 1);
    document.getElementById('ticket-tiers').appendChild(fragment);
    count.value = String(index + 1);
    if (index + 1 >= 20) add.disabled = true;
    document.querySelectorAll('[data-tier]')[index].scrollIntoView({block: 'start', behavior: 'smooth'});
  });
})();
