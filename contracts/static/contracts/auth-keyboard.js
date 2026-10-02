(() => {
  const textTypes = new Set(['text', 'password', 'email', 'tel', 'number', 'url', 'search']);
  document.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
    const input = event.target;
    if (input.tagName !== 'INPUT' || !textTypes.has(input.type)) return;
    const form = input.form;
    if (!form || !(form.matches('.auth-form, .reset-form, [data-auth-form]')
        || form.closest('.auth-shell, .reset-shell, [data-auth-forms]'))) return;

    // Text-field Enter advances focus; buttons retain native keyboard activation.
    event.preventDefault();
    if (event.repeat) return;
    const fields = Array.from(form.querySelectorAll('input')).filter(field =>
      textTypes.has(field.type) && !field.disabled && !field.readOnly
      && !field.closest('[hidden], [inert]') && field.getClientRects().length > 0
    );
    const index = fields.indexOf(input);
    if (index < 0) return;
    (fields[index + 1] || input).focus();
  }, true);
})();
