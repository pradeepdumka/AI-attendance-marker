const pageSize = 10;
const menuKey = "attendance_nav";
const flashKey = "attendance_flash";
const statusOptions = [
  ["ACTIVE", "Active"],
  ["INACTIVE", "Inactive"],
];
const lists = {
  teachers: {
    title: "Teachers",
    addTitle: "Add teachers",
    noun: "teacher",
    path: "/admin/teachers",
    idKey: "teacher_id",
    columns: [
      linkColumn("Name", (row) => `${row.first_name} ${row.last_name}`),
      textColumn("Employee ID", (row) => row.employee_id),
      textColumn("Email", (row) => row.email),
      textColumn("Department", (row) => row.department),
      statusColumn(),
      actionsColumn(),
    ],
    fields: [
      field("first_name", "First name", { required: true }),
      field("last_name", "Last name", { required: true }),
      field("email", "Email", { type: "email", required: true }),
      field("phone", "Phone", { clearable: true }),
      field("employee_id", "Employee ID", { required: true }),
      field("department", "Department", { clearable: true }),
      field("password", "Password", { type: "password", wide: true }),
      field("status", "Status", { type: "select", options: statusOptions }),
    ],
  },
  students: {
    title: "Students",
    addTitle: "Add new student",
    noun: "student",
    path: "/admin/students",
    idKey: "student_id",
    columns: [
      linkColumn("Name", (row) => `${row.first_name} ${row.last_name}`),
      textColumn("Roll number", (row) => row.roll_number),
      textColumn("Email", (row) => row.email),
      textColumn("Class", (row) => className(row.class_id)),
      statusColumn(),
      actionsColumn(),
    ],
    fields: [
      field("first_name", "First name", { required: true }),
      field("last_name", "Last name", { required: true }),
      field("email", "Email", { type: "email", required: true }),
      field("phone", "Phone", { clearable: true }),
      field("roll_number", "Roll number", { required: true }),
      field("date_of_birth", "Date of birth", { type: "date", clearable: true }),
      field("gender", "Gender", {
        type: "select",
        clearable: true,
        options: [["", "Not set"], ["FEMALE", "Female"], ["MALE", "Male"], ["OTHER", "Other"]],
      }),
      field("class_id", "Class", { type: "select", clearable: true, source: "classes", numeric: true }),
      field("password", "Password", { type: "password", wide: true }),
      field("status", "Status", { type: "select", options: statusOptions }),
    ],
  },
  classes: {
    title: "Classes",
    addTitle: "Add new class",
    noun: "class",
    path: "/admin/classes",
    idKey: "class_id",
    columns: [
      linkColumn("Class", (row) => row.name),
      textColumn("Section", (row) => row.section),
      textColumn("Year", (row) => row.academic_year),
      textColumn("Class teacher", (row) => teacherName(row.class_teacher_id)),
      statusColumn(),
      actionsColumn(),
    ],
    fields: [
      field("name", "Class name", { required: true }),
      field("section", "Section", { required: true }),
      field("academic_year", "Academic year", { required: true }),
      field("class_teacher_id", "Class teacher", { type: "select", clearable: true, source: "teachers", numeric: true }),
      field("status", "Status", { type: "select", options: statusOptions }),
    ],
  },
};

const views = {
  home: "home-view",
  list: "list-view",
  detail: "detail-view",
  form: "form-view",
};

let routeTicket = 0;
let route = { name: "home" };
let signedIn = null;
let editing = null;
let pendingDelete = null;
let teachers = [];
let schoolClasses = [];

document.addEventListener("DOMContentLoaded", () => {
  if (!sessionStorage.getItem(tokenKey)) {
    window.location.replace("/login");
    return;
  }
  const toggle = document.getElementById("nav-toggle");
  const scrim = document.getElementById("nav-scrim");
  const narrow = window.matchMedia("(max-width: 800px)").matches;
  const saved = sessionStorage.getItem(menuKey);
  setMenu(saved === null ? narrow : saved === "1");
  toggle.addEventListener("click", () => setMenu(!document.body.classList.contains("nav-collapsed")));
  scrim.addEventListener("click", () => setMenu(true));
  document.getElementById("sidebar").addEventListener("click", (event) => {
    if (event.target.closest("a") && window.matchMedia("(max-width: 800px)").matches) setMenu(true);
  });
  document.addEventListener("click", followAppLink);
  document.getElementById("list-form").addEventListener("submit", (event) => {
    event.preventDefault();
    moveList({ page: 1 });
  });
  document.querySelector("#list-form select").addEventListener("change", () => moveList({ page: 1 }));
  document.getElementById("page-prev").addEventListener("click", () => turn(-1));
  document.getElementById("page-next").addEventListener("click", () => turn(1));
  document.getElementById("editor").addEventListener("submit", saveEditor);
  document.getElementById("editor-cancel").addEventListener("click", cancelForm);
  document.getElementById("detail-delete").addEventListener("click", askDelete);
  document.getElementById("delete-yes").addEventListener("click", confirmDelete);
  document.getElementById("delete-no").addEventListener("click", hideDelete);
  window.addEventListener("popstate", showRoute);
  window.addEventListener("hashchange", () => {
    if (redirectLegacy()) showRoute();
  });
  redirectLegacy();
  loadUser();
  showRoute();
});

function setMenu(collapsed) {
  document.body.classList.toggle("nav-collapsed", collapsed);
  sessionStorage.setItem(menuKey, collapsed ? "1" : "0");
  document.getElementById("nav-toggle").setAttribute("aria-expanded", String(!collapsed));
  const narrow = window.matchMedia("(max-width: 800px)").matches;
  document.getElementById("nav-scrim").hidden = !(narrow && !collapsed);
}

function followAppLink(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest("a[href]");
  if (!link || link.target) return;
  const url = new URL(link.href, location.origin);
  if (url.origin !== location.origin || !url.pathname.startsWith("/dashboard")) return;
  event.preventDefault();
  go(`${url.pathname}${url.search}`);
}

function redirectLegacy() {
  const next = { dashboard: "/dashboard", teachers: "/dashboard/teachers", students: "/dashboard/students", classes: "/dashboard/classes" }[location.hash.slice(1)];
  if (!next || location.pathname !== "/dashboard") return false;
  history.replaceState(null, "", next);
  return true;
}

function parseRoute() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[0] !== "dashboard") return { name: "missing" };
  if (parts.length === 1) return { name: "home" };
  const resource = parts[1];
  if (!lists[resource]) return { name: "missing" };
  if (parts.length === 2) return { name: "list", resource };
  if (parts.length === 3 && parts[2] === "new") return { name: "form", resource };
  if (parts.length === 3 && /^\d+$/.test(parts[2])) return { name: "detail", resource, id: parts[2] };
  if (parts.length === 4 && /^\d+$/.test(parts[2]) && parts[3] === "edit") return { name: "form", resource, id: parts[2] };
  return { name: "missing" };
}

function go(path) {
  if (`${location.pathname}${location.search}` === path) {
    showRoute();
    return;
  }
  history.pushState(null, "", path);
  showRoute();
}

function showRoute() {
  const ticket = ++routeTicket;
  route = parseRoute();
  editing = null;
  pendingDelete = null;
  hideDelete();
  showMessage("");
  markCurrent();
  setHeading(pageTitle());
  for (const [name, id] of Object.entries(views)) {
    document.getElementById(id).hidden = name !== route.name;
  }
  const editor = document.getElementById("editor");
  if (editor) editor.hidden = route.name !== "form";
  if (route.name === "home") loadHome(ticket);
  else if (route.name === "list") loadList(ticket);
  else if (route.name === "detail") loadDetail(ticket);
  else if (route.name === "form") loadForm(ticket);
  else showMessage("That page was not found.");
  revealFlash(ticket);
}

function revealFlash(ticket) {
  const text = sessionStorage.getItem(flashKey);
  if (!text) return;
  sessionStorage.removeItem(flashKey);
  queueMicrotask(() => {
    if (ticket === routeTicket) showMessage(text, true);
  });
}

function pageTitle() {
  if (route.name === "home" || route.name === "missing") return "Dashboard";
  const list = lists[route.resource];
  if (route.name === "list") return list.title;
  if (route.name === "detail") return "Details";
  return route.id ? `Edit ${list.noun}` : list.addTitle;
}

function setHeading(text) {
  document.getElementById("view-title").textContent = text;
  document.title = `${text} · AI Attendance Marker`;
}

function markCurrent() {
  const current = route.name === "form" && !route.id
    ? `/dashboard/${route.resource}/new`
    : route.resource
      ? `/dashboard/${route.resource}`
      : "/dashboard";
  document.querySelectorAll(".sidebar a[data-route]").forEach((link) => {
    if (link.dataset.route === current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

async function loadUser() {
  const { response, data } = await request("/auth/me");
  if (response.status === 401) {
    sessionStorage.removeItem(tokenKey);
    window.location.replace("/login");
    return;
  }
  if (!response.ok) return;
  signedIn = data;
  document.getElementById("user-name").textContent = `${data.first_name} ${data.last_name}`;
  document.getElementById("user-role").textContent = label(data.role);
  document.getElementById("user-initials").textContent = initials(data.first_name, data.last_name);
  if (route.name === "home") paintHello(document.getElementById("dashboard-hello").dataset.blocked === "1");
}

function paintHello(blocked) {
  const hello = document.getElementById("dashboard-hello");
  hello.dataset.blocked = blocked ? "1" : "0";
  const welcome = signedIn ? `Welcome back, ${signedIn.first_name}.` : "Welcome back.";
  hello.textContent = blocked
    ? `${welcome} Teacher, student, and class lists are open to an admin.`
    : welcome;
}

async function loadHome(ticket) {
  const cards = document.getElementById("stat-cards");
  cards.replaceChildren();
  const entries = await Promise.all(
    Object.entries(lists).map(async ([key, list]) => {
      const { response, data } = await request(`${list.path}?page=1&page_size=1`);
      return [key, list.title, response.ok ? data.total : null, response.status];
    }),
  );
  if (ticket !== routeTicket) return;
  paintHello(entries.some((entry) => entry[3] === 403));
  for (const [key, title, total] of entries) {
    const item = document.createElement("li");
    const heading = document.createElement("h3");
    const link = document.createElement("a");
    link.href = `/dashboard/${key}`;
    link.textContent = title;
    heading.append(link);
    const count = document.createElement("p");
    count.className = "stat";
    count.textContent = total === null ? "—" : String(total);
    item.append(heading, count);
    cards.append(item);
  }
}

function listQuery() {
  const params = new URLSearchParams(location.search);
  return {
    page: Math.max(1, Number(params.get("page")) || 1),
    search: params.get("search") || "",
    status: params.get("status") || "",
  };
}

function listPath(resource, state) {
  const params = new URLSearchParams();
  if (state.page > 1) params.set("page", String(state.page));
  if (state.search) params.set("search", state.search);
  if (state.status) params.set("status", state.status);
  const query = params.toString();
  return `/dashboard/${resource}${query ? `?${query}` : ""}`;
}

function moveList(patch) {
  if (route.name !== "list") return;
  const form = document.getElementById("list-form");
  const state = listQuery();
  go(listPath(route.resource, {
    page: patch.page ?? state.page,
    search: value(form, "search"),
    status: value(form, "status"),
  }));
}

function turn(step) {
  if (route.name !== "list") return;
  const state = listQuery();
  go(listPath(route.resource, { ...state, page: Math.max(1, state.page + step) }));
}

async function loadList(ticket) {
  const list = lists[route.resource];
  const state = listQuery();
  const form = document.getElementById("list-form");
  form.elements.search.value = state.search;
  form.elements.status.value = state.status;
  const params = new URLSearchParams({ page: String(state.page), page_size: String(pageSize) });
  if (state.search) params.set("search", state.search);
  if (state.status) params.set("status", state.status);
  renderHead(list.columns);
  const body = document.getElementById("list-body");
  body.replaceChildren(messageRow(list.columns.length, "Loading…"));
  const { response, data } = await request(`${list.path}?${params}`);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    body.replaceChildren(messageRow(list.columns.length, "No data to display"));
    document.getElementById("list-summary").textContent = "Showing 0 – 0 of 0";
    setPager(state.page, 0);
    showMessage(response.status === 403 ? "Admin access is required for this list." : errorText(data));
    return;
  }
  await prepareSources(list);
  if (ticket !== routeTicket) return;
  renderRows(list.columns, data.items);
  const total = data.total || 0;
  const start = total === 0 ? 0 : (state.page - 1) * pageSize + 1;
  const end = Math.min(state.page * pageSize, total);
  document.getElementById("list-summary").textContent = `Showing ${start} – ${end} of ${total}`;
  setPager(state.page, total);
}

function setPager(page, total) {
  document.getElementById("page-prev").disabled = page <= 1;
  document.getElementById("page-next").disabled = page * pageSize >= total;
}

function renderHead(columns) {
  const row = document.createElement("tr");
  for (const column of columns) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = column.label;
    row.append(cell);
  }
  document.getElementById("list-head").replaceChildren(row);
}

function renderRows(columns, items) {
  const body = document.getElementById("list-body");
  if (!items.length) {
    body.replaceChildren(messageRow(columns.length, "No data to display"));
    return;
  }
  body.replaceChildren(...items.map((item) => {
    const row = document.createElement("tr");
    row.append(...columns.map((column) => column.cell(item)));
    return row;
  }));
}

function messageRow(span, text) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = span;
  cell.className = "empty";
  cell.textContent = text;
  row.append(cell);
  return row;
}

function linkColumn(label, read) {
  return {
    label,
    cell(row) {
      const cell = document.createElement("td");
      const link = document.createElement("a");
      const text = read(row);
      link.href = `/dashboard/${route.resource}/${row[lists[route.resource].idKey]}`;
      link.textContent = text ? String(text) : "—";
      cell.append(link);
      return cell;
    },
  };
}

function textColumn(label, read) {
  return {
    label,
    cell(row) {
      const cell = document.createElement("td");
      const text = read(row);
      cell.textContent = text ? String(text) : "—";
      return cell;
    },
  };
}

function statusColumn() {
  return { label: "Status", cell: (row) => statusCell(row.status) };
}

function actionsColumn() {
  return {
    label: "Actions",
    cell(row) {
      const list = lists[route.resource];
      const cell = document.createElement("td");
      cell.className = "row-actions";
      const edit = document.createElement("a");
      edit.className = "text-button";
      edit.href = `/dashboard/${route.resource}/${row[list.idKey]}/edit`;
      edit.textContent = "Edit";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button danger";
      remove.textContent = "Delete";
      remove.addEventListener("click", () => askDelete(row));
      cell.append(edit, remove);
      return cell;
    },
  };
}

function statusCell(status) {
  const cell = document.createElement("td");
  cell.append(statusPill(status));
  return cell;
}

function statusPill(status) {
  const pill = document.createElement("span");
  const active = status === "ACTIVE";
  pill.className = `pill ${active ? "pill-present" : "pill-absent"}`;
  pill.textContent = active ? "Active" : "Inactive";
  return pill;
}

function initials(first, last) {
  return `${first || ""} ${last || ""}`.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase() || "?";
}

function label(role) {
  const text = String(role || "").toLowerCase();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

function field(name, labelText, options = {}) {
  return { name, label: labelText, ...options };
}

async function loadDetail(ticket) {
  const list = lists[route.resource];
  document.getElementById("detail-fields").replaceChildren();
  document.querySelector("#detail-view .actions").hidden = true;
  const { response, data } = await request(`${list.path}/${route.id}`);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    showMessage(response.status === 403 ? "Admin access is required." : errorText(data));
    return;
  }
  await prepareSources(list);
  if (ticket !== routeTicket) return;
  pendingDelete = data;
  renderDetail(list, data);
}

function renderDetail(list, row) {
  const blocks = list.fields.filter((item) => item.type !== "password").map((item) => {
    const wrap = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = item.label;
    const valueNode = document.createElement("dd");
    if (item.name === "status") valueNode.append(statusPill(row.status));
    else valueNode.textContent = shown(item, row);
    wrap.append(term, valueNode);
    return wrap;
  });
  document.getElementById("detail-fields").replaceChildren(...blocks);
  document.getElementById("detail-edit").href = `/dashboard/${route.resource}/${row[list.idKey]}/edit`;
  document.querySelector("#detail-view .actions").hidden = false;
  setHeading(recordTitle(row));
}

function shown(item, row) {
  const raw = row[item.name];
  if (raw == null || raw === "") return "—";
  if (item.source === "teachers") return teacherName(raw) || "—";
  if (item.source === "classes") return className(raw) || "—";
  const match = item.options?.find(([optionValue]) => optionValue === raw);
  return match ? match[1] : String(raw);
}

function recordTitle(row) {
  if (row.first_name) return `${row.first_name} ${row.last_name}`;
  if (row.name) return `${row.name}-${row.section}`;
  return "Details";
}

async function loadForm(ticket) {
  const list = lists[route.resource];
  const editor = document.getElementById("editor");
  editor.hidden = true;
  let row = null;
  if (route.id) {
    const { response, data } = await request(`${list.path}/${route.id}`);
    if (ticket !== routeTicket) return;
    if (!response.ok) {
      showMessage(response.status === 403 ? "Admin access is required." : errorText(data));
      return;
    }
    row = data;
  }
  await prepareSources(list);
  if (ticket !== routeTicket) return;
  editing = row;
  document.getElementById("editor-fields").replaceChildren(...list.fields.map((item) => fieldControl(item, row)));
  editor.hidden = false;
  editor.querySelector("input, select")?.focus();
}

function fieldControl(item, row) {
  const wrap = document.createElement("label");
  wrap.className = item.wide ? "field editor-wide" : "field";
  const caption = document.createElement("span");
  caption.textContent = item.label;
  wrap.append(caption);
  const control = item.type === "select" ? document.createElement("select") : document.createElement("input");
  control.name = item.name;
  if (control.tagName === "INPUT") {
    control.type = item.type || "text";
    if (item.type === "number") control.min = "1";
    if (item.type === "password") {
      control.autocomplete = "new-password";
      control.maxLength = 128;
    }
  } else {
    const choices = item.source === "teachers"
      ? teacherChoices(row)
      : item.source === "classes"
        ? classChoices(row)
        : item.options;
    for (const [optionValue, optionLabel] of choices) {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionLabel;
      control.append(option);
    }
  }
  const current = row ? row[item.name] : item.name === "status" ? "ACTIVE" : "";
  control.value = current == null ? "" : String(current);
  wrap.append(control);
  if (item.type === "password") {
    const hint = document.createElement("small");
    hint.textContent = row
      ? "Leave blank to keep the current password."
      : "8 to 128 characters, and not the same as the email.";
    wrap.append(hint);
  } else if (item.source === "teachers" && teachers.length === 0) {
    const hint = document.createElement("small");
    hint.textContent = "No teachers yet. Add one under Teacher management.";
    wrap.append(hint);
  } else if (item.source === "classes" && !schoolClasses.some((schoolClass) => schoolClass.status === "ACTIVE")) {
    const hint = document.createElement("small");
    hint.textContent = "No classes yet. Add one under Class management.";
    wrap.append(hint);
  }
  return wrap;
}

function cancelForm() {
  if (route.name !== "form") return;
  go(route.id ? `/dashboard/${route.resource}/${route.id}` : `/dashboard/${route.resource}`);
}

async function saveEditor(event) {
  event.preventDefault();
  const list = lists[route.resource];
  if (!list || route.name !== "form") return;
  const collected = collectEditor(list, editing);
  if (collected.error) {
    showMessage(collected.error);
    return;
  }
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  const path = editing ? `${list.path}/${editing[list.idKey]}` : list.path;
  const method = editing ? "PATCH" : "POST";
  try {
    const { response, data } = await request(path, collected.body, method);
    if (!response.ok) {
      showMessage(errorText(data));
      return;
    }
    sessionStorage.setItem(flashKey, `${capitalize(list.noun)} saved.`);
    go(`/dashboard/${route.resource}/${data[list.idKey]}`);
  } catch {
    showMessage("The server could not be reached.");
  } finally {
    button.disabled = false;
  }
}

function askDelete(row) {
  const list = lists[route.resource];
  const target = list && row && row[list.idKey] != null ? row : pendingDelete;
  if (!list || !target || (route.name !== "list" && route.name !== "detail")) return;
  pendingDelete = target;
  document.getElementById("delete-text").textContent = `Delete ${recordTitle(target)}? The record stays on file as inactive.`;
  document.getElementById("delete-confirm").hidden = false;
}

function hideDelete() {
  const box = document.getElementById("delete-confirm");
  if (box) box.hidden = true;
}

async function confirmDelete() {
  const list = lists[route.resource];
  const row = pendingDelete;
  if (!list || !row || (route.name !== "list" && route.name !== "detail")) return;
  const button = document.getElementById("delete-yes");
  button.disabled = true;
  try {
    const { response, data } = await request(`${list.path}/${row[list.idKey]}`, null, "DELETE");
    if (!response.ok) {
      showMessage(errorText(data));
      return;
    }
    sessionStorage.setItem(flashKey, `${capitalize(list.noun)} deleted.`);
    if (route.name === "list") showRoute();
    else go(`/dashboard/${route.resource}`);
  } catch {
    showMessage("The server could not be reached.");
  } finally {
    button.disabled = false;
  }
}

function collectEditor(list, row) {
  const form = document.getElementById("editor");
  const body = {};
  for (const item of list.fields) {
    const raw = value(form, item.name);
    if (item.type === "password") {
      if (!raw) {
        if (!row) return { error: "Enter a password." };
        continue;
      }
      if (raw.length < 8 || raw.length > 128) return { error: "Password must be 8 to 128 characters." };
      body.password = raw;
      continue;
    }
    if (!raw) {
      if (item.required) return { error: `Enter ${item.label.toLowerCase()}.` };
      if (item.clearable && row && row[item.name] != null && row[item.name] !== "") body[item.name] = null;
      continue;
    }
    if (item.numeric || item.type === "number") {
      const number = Number(raw);
      if (!Number.isInteger(number) || number < 1) return { error: `Choose a valid ${item.label.toLowerCase()}.` };
      body[item.name] = number;
      continue;
    }
    body[item.name] = raw;
  }
  if (body.password && body.email && body.password.toLowerCase() === String(body.email).toLowerCase()) {
    return { error: "Password must not match the email address." };
  }
  return { body };
}

function capitalize(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

async function prepareSources(list) {
  if (list.fields.some((item) => item.source === "teachers")) await refreshTeachers();
  if (list.fields.some((item) => item.source === "classes")) await refreshClasses();
}

async function refreshTeachers() {
  const rows = [];
  let pageNumber = 1;
  while (pageNumber <= 20) {
    const { response, data } = await request(`/admin/teachers?page=${pageNumber}&page_size=100`);
    if (!response.ok) return;
    rows.push(...(data.items || []));
    if (!data.items?.length || rows.length >= (data.total || 0)) break;
    pageNumber += 1;
  }
  teachers = rows.sort((left, right) =>
    `${left.first_name} ${left.last_name}`.localeCompare(`${right.first_name} ${right.last_name}`),
  );
}

function teacherChoices(row) {
  const choices = [["", "Not assigned"]];
  const seen = new Set();
  for (const teacher of teachers) {
    seen.add(teacher.teacher_id);
    choices.push([String(teacher.teacher_id), `${teacher.teacher_id} · ${teacher.first_name} ${teacher.last_name}`]);
  }
  const current = row?.class_teacher_id;
  if (current && !seen.has(current)) choices.push([String(current), `Teacher ${current}`]);
  return choices;
}

function teacherName(id) {
  if (!id) return "";
  const teacher = teachers.find((item) => item.teacher_id === id);
  return teacher ? `${teacher.first_name} ${teacher.last_name}` : `Teacher ${id}`;
}

async function refreshClasses() {
  const rows = [];
  let pageNumber = 1;
  while (pageNumber <= 20) {
    const { response, data } = await request(`/admin/classes?page=${pageNumber}&page_size=100`);
    if (!response.ok) return;
    rows.push(...(data.items || []));
    if (!data.items?.length || rows.length >= (data.total || 0)) break;
    pageNumber += 1;
  }
  schoolClasses = rows.sort((left, right) => classLabel(left).localeCompare(classLabel(right)));
}

function classChoices(row) {
  const choices = [["", "Not assigned"]];
  const seen = new Set();
  for (const schoolClass of schoolClasses) {
    if (schoolClass.status !== "ACTIVE" && schoolClass.class_id !== row?.class_id) continue;
    seen.add(schoolClass.class_id);
    choices.push([String(schoolClass.class_id), classLabel(schoolClass)]);
  }
  const current = row?.class_id;
  if (current && !seen.has(current)) choices.push([String(current), `Class ${current}`]);
  return choices;
}

function classLabel(schoolClass) {
  return `${schoolClass.name}-${schoolClass.section}`;
}

function className(id) {
  if (!id) return "";
  const schoolClass = schoolClasses.find((item) => item.class_id === id);
  return schoolClass ? classLabel(schoolClass) : `Class ${id}`;
}
