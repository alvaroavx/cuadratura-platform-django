document.querySelectorAll('[data-period-picker]').forEach((picker) => {
  const trigger = picker.querySelector('summary');
  const year = picker.querySelector('select[name="year"]');
  const steps = picker.querySelectorAll('[data-year-step]');
  const months = picker.querySelectorAll('button[name="month"]');
  const activity = JSON.parse(picker.querySelector('script[type="application/json"]').textContent);
  const firstYear = year.options[0].value;

  const update = () => {
    const enabled = activity[year.value] || [];
    const firstMonth = year.value === firstYear ? enabled[0] : 1;
    months.forEach((button) => {
      const month = Number(button.value);
      button.disabled = !enabled.includes(month);
      const beforeHistory = month < firstMonth;
      button.classList.toggle('period-month-before-history', beforeHistory);
      if (beforeHistory) button.setAttribute('aria-hidden', 'true');
      else button.removeAttribute('aria-hidden');
      if (year.value === picker.dataset.activeYear && button.value === picker.dataset.activeMonth) {
        button.setAttribute('aria-current', 'date');
      } else {
        button.removeAttribute('aria-current');
      }
    });
    steps.forEach((button) => {
      const next = year.selectedIndex + Number(button.dataset.yearStep);
      button.disabled = next < 0 || next >= year.options.length;
    });
  };

  steps.forEach((button) => {
    button.hidden = year.options.length < 2;
    button.addEventListener('click', () => {
      year.selectedIndex += Number(button.dataset.yearStep);
      update();
      if (button.disabled) year.focus();
    });
  });
  year.addEventListener('change', update);
  picker.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && picker.open) {
      event.preventDefault();
      picker.open = false;
      trigger.focus();
    }
  });
  document.addEventListener('click', (event) => {
    if (picker.open && !picker.contains(event.target)) picker.open = false;
  });
  update();
});
