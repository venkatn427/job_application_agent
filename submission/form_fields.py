"""Deterministic local form answers and required-field checks; no AI calls."""
import re


def _normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).strip()


def answer_for(label, profile):
    question = _normalize(label)
    for key, value in profile.get('screening_defaults', {}).items():
        if not key.startswith('_') and _normalize(key) == question:
            return str(value)
    personal = profile.get('personal', profile)
    professional = profile.get('professional', {})
    authorization = profile.get('work_authorization', {})
    values = {
        'first name': personal.get('first_name'), 'last name': personal.get('last_name'),
        'email': personal.get('email'), 'phone': personal.get('phone'),
        'location city': personal.get('city'), 'city': personal.get('city'),
        'country': personal.get('country'),
        'who is your current or previous job title': professional.get('current_title') or profile.get('current_title'),
        'what is your current or previous job title': professional.get('current_title') or profile.get('current_title'),
        'who is your current or previous employer': professional.get('current_employer'),
        'please select the country where you currently reside': personal.get('country'),
    }
    if question in values:
        value = values[question]
        return {'US': 'United States', 'USA': 'United States'}.get(value, value) if isinstance(value, str) else value
    if question.startswith('are you authorized to work'):
        value = authorization.get('authorized_to_work_us')
    elif 'sponsor' in question and ('now or in the future' in question or 'now or in future' in question):
        value = authorization.get('require_sponsorship')
    else:
        value = None
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, str) and value.lower() in ('yes', 'no', 'true', 'false'):
        return 'Yes' if value.lower() in ('yes', 'true') else 'No'
    years = professional.get('years_experience', profile.get('years_experience'))
    match = re.search(r'(more than|less than) (\d+) years of industry experience', question)
    if match and years is not None:
        try:
            qualifies = float(years) > int(match[2]) if match[1] == 'more than' else float(years) < int(match[2])
            return 'Yes' if qualifies else 'No'
        except (TypeError, ValueError):
            pass
    return None


def fill_known_fields(scope, profile):
    fields = scope.locator('input:not([type="file"]):not([type="hidden"]), textarea, select')
    for index in range(fields.count()):
        field = fields.nth(index)
        try:
            if not field.is_visible() or not field.is_editable():
                continue
            info = field.evaluate('''e => ({label:e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.name || e.id,
                role:e.getAttribute('role'),type:e.type,tag:e.tagName,
                selected:!!e.closest('.select__value-container')?.querySelector('.select__single-value,.select__multi-value')})''')
            if field.input_value() or info['selected'] or info['type'] in ('checkbox', 'radio', 'search', 'password'):
                continue
            answer = answer_for(info['label'], profile)
            if answer is None or answer == '':
                continue
            if info['tag'] == 'SELECT':
                field.select_option(label=answer)
            elif info['role'] == 'combobox':
                field.fill(answer)
                option = scope.get_by_role('option', name=re.compile(r'^' + re.escape(answer) + r'$', re.I))
                if option.count() == 1:
                    option.click(timeout=3000)
                else:
                    field.fill('')
                    field.press('Escape')
            else:
                field.fill(str(answer))
        except Exception:
            # A portal-specific control remains available for manual completion.
            continue


def missing_required(scope):
    return scope.evaluate('''() => {
      const missing=[]; const groups=new Set();
      for(const e of document.querySelectorAll('input[required],textarea[required],select[required],[aria-required="true"]')) {
        const rect=e.getBoundingClientRect();
        if(!rect.width || !rect.height || getComputedStyle(e).visibility==='hidden' || e.disabled || e.type==='hidden' || e.getAttribute('aria-hidden')==='true') continue;
        if(e.closest('.select__value-container')?.querySelector('.select__single-value,.select__multi-value')) continue;
        if(e.type==='checkbox'||e.type==='radio') {
          if(e.name && groups.has(e.name)) continue;
          if(e.name) groups.add(e.name);
          const group=e.name?[...document.getElementsByName(e.name)]:[e];
          if(group.some(x=>x.checked)) continue;
        } else if(e.type==='file' ? e.files?.length : e.value?.trim()) continue;
        missing.push(e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.name || e.id || 'Required field');
      }
      return [...new Set(missing)];
    }''')
