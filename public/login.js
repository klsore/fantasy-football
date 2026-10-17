document.getElementById('login-form').onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget, button = form.querySelector('button');
  button.disabled = true;
  try {
    const fields = new FormData(form);
    const response = await fetch('/private/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:fields.get('username'),password:fields.get('password')})});
    form.elements.password.value = '';
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Sign-in failed.');
    location.assign('/private/');
  } catch(error) { document.getElementById('login-error').textContent = error.message; }
  finally { button.disabled = false; }
};
