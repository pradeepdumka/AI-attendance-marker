const tokenKey = "attendance_token";

document.addEventListener("DOMContentLoaded", () => {
  const token = sessionStorage.getItem(tokenKey);
  document.querySelectorAll("[data-user]").forEach((node) => {
    node.hidden = !token;
  });
  document.querySelectorAll("[data-guest], [data-guest-cta]").forEach((node) => {
    node.hidden = Boolean(token);
  });
  document.querySelector("[data-logout]")?.addEventListener("click", logout);

  const page = document.body.dataset.page;
  if (page === "login") document.getElementById("login-form")?.addEventListener("submit", onLogin);
  if (page === "register") document.getElementById("register-form")?.addEventListener("submit", onRegister);
  if (page === "account") loadAccount();
});

async function onLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const email = value(form, "email");
  const password = value(form, "password");
  if (!email || !password) {
    showMessage("Enter your email and password.");
    return;
  }
  await submit(form, "/auth/login", { email, password }, async (data) => {
    sessionStorage.setItem(tokenKey, data.access_token);
    window.location.assign("/dashboard");
  });
}

async function onRegister(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = {
    first_name: value(form, "first_name"),
    last_name: value(form, "last_name"),
    email: value(form, "email"),
    password: value(form, "password"),
    role: form.role.value,
  };
  const phone = value(form, "phone");
  if (phone) body.phone = phone;
  if (body.password.length < 8) {
    showMessage("Password must be 8 to 128 characters.");
    return;
  }
  if (body.password.toLowerCase() === body.email.toLowerCase()) {
    showMessage("Password must not match the email address.");
    return;
  }
  await submit(form, "/auth/register", body, async () => {
    const login = await request("/auth/login", { email: body.email, password: body.password });
    if (!login.response.ok) {
      showMessage("Account created. Sign in with the same email and password.", true);
      return;
    }
    sessionStorage.setItem(tokenKey, login.data.access_token);
    window.location.assign("/dashboard");
  });
}

async function loadAccount() {
  if (!sessionStorage.getItem(tokenKey)) {
    window.location.replace("/login");
    return;
  }
  const { response, data } = await request("/auth/me");
  const message = document.getElementById("form-message");
  if (!response.ok) {
    sessionStorage.removeItem(tokenKey);
    window.location.replace("/login");
    return;
  }
  message.hidden = true;
  document.getElementById("account-name").textContent = `${data.first_name} ${data.last_name}`;
  document.getElementById("account-email").textContent = data.email;
  document.getElementById("account-role").textContent = data.role;
  document.getElementById("account-details").hidden = false;
}

async function submit(form, path, body, onOk) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  showMessage("");
  try {
    const { response, data } = await request(path, body);
    if (!response.ok) {
      showMessage(errorText(data));
      return;
    }
    await onOk(data);
  } catch {
    showMessage("The server could not be reached.");
  } finally {
    button.disabled = false;
  }
}

async function request(path, body, method) {
  const verb = method || (body ? "POST" : "GET");
  const headers = {};
  const token = sessionStorage.getItem(tokenKey);
  if (token) headers.Authorization = `Bearer ${token}`;
  const send = body != null && verb !== "GET";
  if (send) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: verb,
    headers,
    body: send ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  return { response, data };
}

function errorText(data) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((item) => item.msg || "Check the form").join(" ");
  }
  return "Something went wrong. Try again.";
}

function showMessage(text, ok) {
  const node = document.getElementById("form-message");
  if (!node) return;
  node.hidden = text === "";
  node.textContent = text;
  node.classList.toggle("is-ok", Boolean(ok));
}

function value(form, name) {
  return String(form.elements[name].value || "").trim();
}

function logout() {
  sessionStorage.removeItem(tokenKey);
  window.location.assign("/");
}
