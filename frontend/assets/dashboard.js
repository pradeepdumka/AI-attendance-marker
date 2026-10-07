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
      field("employee_id", "Employee ID", { generated: "T" }),
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
      field("roll_number", "Roll number", { generated: "S" }),
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
  subjects: {
    title: "Subjects",
    addTitle: "Add subject",
    noun: "subject",
    path: "/admin/subjects",
    idKey: "subject_id",
    columns: [
      linkColumn("Subject", (row) => row.name),
      textColumn("Code", (row) => row.code),
      textColumn("Class", (row) => className(row.class_id)),
      textColumn("Teacher", (row) => teacherName(row.teacher_id)),
      statusColumn(),
      actionsColumn(),
    ],
    fields: [
      field("name", "Subject name", { required: true }),
      field("code", "Code", { required: true }),
      field("class_id", "Class", { type: "select", required: true, source: "classes", numeric: true }),
      field("teacher_id", "Teacher", { type: "select", clearable: true, source: "teachers", numeric: true }),
      field("status", "Status", { type: "select", options: statusOptions }),
    ],
  },
};

const teacherLists = {
  classes: {
    title: "My Classes",
    noun: "class",
    path: "/teacher/classes",
    idKey: "class_id",
    columns: [
      linkColumn("Class", (row) => row.name),
      textColumn("Section", (row) => row.section),
      textColumn("Year", (row) => row.academic_year),
    ],
    fields: [
      field("name", "Class name"),
      field("section", "Section"),
      field("academic_year", "Academic year"),
    ],
  },
  subjects: {
    title: "My Subjects",
    noun: "subject",
    path: "/teacher/subjects",
    idKey: "subject_id",
    columns: [
      linkColumn("Subject", (row) => row.name),
      textColumn("Code", (row) => row.code),
      textColumn("Class", (row) => className(row.class_id)),
    ],
    fields: [
      field("name", "Subject"),
      field("code", "Code"),
      field("class_id", "Class"),
    ],
  },
};

const studentLists = {
  subjects: {
    title: "Subjects",
    noun: "subject",
    path: "/student/subjects",
    idKey: "subject_id",
    columns: [
      textColumn("Subject", (row) => row.name),
      textColumn("Code", (row) => row.code),
      textColumn("Class", (row) => className(row.class_id)),
    ],
    fields: [],
  },
};

const viewFor = {
  home: "home-view",
  list: "list-view",
  detail: "detail-view",
  form: "form-view",
  faces: "face-list-view",
  face: "face-form-view",
  attendance: "attendance-view",
  history: "attendance-view",
  mark: "mark-view",
  reports: "reports-view",
  profile: "profile-view",
  percentage: "percentage-view",
  calendar: "calendar-view",
};

let routeTicket = 0;
let route = { name: "home" };
let signedIn = null;
let editing = null;
let pendingDelete = null;
let teachers = [];
let schoolClasses = [];
let lessonClasses = [];
let lessonSubjects = [];
let lessonStudents = [];
let scannerStream = null;
let scannerTimer = null;
let scannerBusy = false;
const scannerIntervalMs = 2200;
let faceCameraStream = null;
let faceCaptureBlob = null;
let facePreviewUrl = "";
let faceSource = "upload";
let faceCameraState = "idle";
let faceAddLocked = false;
let openSessionId = null;

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
  document.getElementById("list-form").addEventListener("change", (event) => {
    if (event.target.matches("select")) moveList({ page: 1 });
  });
  document.getElementById("page-prev").addEventListener("click", () => turn(-1));
  document.getElementById("page-next").addEventListener("click", () => turn(1));
  document.getElementById("editor").addEventListener("submit", saveEditor);
  document.getElementById("editor-cancel").addEventListener("click", cancelForm);
  document.getElementById("detail-delete").addEventListener("click", askDelete);
  document.getElementById("delete-yes").addEventListener("click", confirmDelete);
  document.getElementById("delete-no").addEventListener("click", hideDelete);
  document.getElementById("face-list-form").addEventListener("submit", (event) => {
    event.preventDefault();
    go(facePath({ page: 1, search: value(event.currentTarget, "search") }));
  });
  document.getElementById("face-prev").addEventListener("click", () => turnFace(-1));
  document.getElementById("face-next").addEventListener("click", () => turnFace(1));
  document.getElementById("face-form").addEventListener("submit", (event) => {
    event.preventDefault();
    saveFace("POST");
  });
  document.getElementById("face-replace").addEventListener("click", () => saveFace("PUT"));
  document.getElementById("face-cancel").addEventListener("click", () => go("/dashboard/faces"));
  document.getElementById("face-source-upload").addEventListener("click", () => setFaceSource("upload"));
  document.getElementById("face-source-camera").addEventListener("click", () => setFaceSource("camera"));
  document.getElementById("face-camera-start").addEventListener("click", startFaceCamera);
  document.getElementById("face-camera-snap").addEventListener("click", captureFacePhoto);
  document.getElementById("face-camera-retake").addEventListener("click", retakeFacePhoto);
  document.getElementById("attendance-form").addEventListener("submit", (event) => event.preventDefault());
  document.getElementById("attendance-form").addEventListener("change", moveAttendance);
  document.getElementById("attendance-prev").addEventListener("click", () => turnAttendance(-1));
  document.getElementById("attendance-next").addEventListener("click", () => turnAttendance(1));
  document.getElementById("camera-form").addEventListener("submit", (event) => event.preventDefault());
  document.getElementById("scanner-start").addEventListener("click", startScanner);
  document.getElementById("scanner-stop").addEventListener("click", () => stopScanner("Scanner stopped."));
  document.getElementById("manual-form").addEventListener("submit", saveManualMark);
  document.querySelector("#camera-form select[name=class_id]").addEventListener("change", (event) => {
    if (scannerStream) stopScanner("Class changed. Start the scanner again.");
    fillSubjectSelect(
      document.querySelector("#camera-form select[name=subject_id]"),
      event.currentTarget.value,
      "",
      "Choose a subject",
    );
  });
  document.querySelector("#camera-form select[name=subject_id]").addEventListener("change", () => {
    if (scannerStream) stopScanner("Subject changed. Start the scanner again.");
  });
  document.querySelector("#manual-form select[name=class_id]").addEventListener("change", (event) => {
    fillSubjectSelect(
      document.querySelector("#manual-form select[name=subject_id]"),
      event.currentTarget.value,
      "",
      "Choose a subject",
    );
    fillStudentSelect(
      document.querySelector("#manual-form select[name=student_id]"),
      event.currentTarget.value,
    );
  });
  document.getElementById("report-form").addEventListener("change", moveReport);
  document.getElementById("report-form").addEventListener("submit", (event) => event.preventDefault());
  document.getElementById("report-export").addEventListener("click", exportReport);
  document.getElementById("calendar-form").addEventListener("submit", (event) => event.preventDefault());
  document.getElementById("calendar-form").addEventListener("change", moveCalendar);
  window.addEventListener("popstate", showRoute);
  window.addEventListener("pagehide", () => {
    stopFaceCamera();
    stopScanner("", { quiet: true });
  });
  window.addEventListener("hashchange", () => {
    if (redirectLegacy()) showRoute();
  });
  redirectLegacy();
  boot();
});

async function boot() {
  const user = await loadUser();
  if (!user) {
    if (sessionStorage.getItem(tokenKey)) showMessage("The server could not be reached.");
    return;
  }
  applyRole(user.role);
  showRoute();
}

function applyRole(role) {
  document.querySelectorAll("[data-roles]").forEach((node) => {
    node.hidden = !node.dataset.roles.split(/\s+/).includes(role);
  });
}

function accessBlock(next) {
  if (!signedIn) return null;
  if (signedIn.role === "STUDENT") return studentAllowed(next) ? null : "student";
  if (signedIn.role === "TEACHER" && !teacherAllowed(next)) {
    return studentRoute(next) ? "student-only" : "teacher";
  }
  if (signedIn.role === "ADMIN" && studentRoute(next)) return "student-only";
  if (signedIn.role === "ADMIN" && (next.name === "history" || next.name === "reports")) return "admin";
  return null;
}

function studentAllowed(next) {
  if (next.name === "home" || next.name === "missing") return true;
  if (next.name === "profile" || next.name === "attendance" || next.name === "percentage" || next.name === "calendar") {
    return true;
  }
  return next.name === "list" && next.resource === "subjects";
}

function studentRoute(next) {
  return next.name === "profile" || next.name === "percentage" || next.name === "calendar";
}

function teacherAllowed(next) {
  if (next.name === "home" || next.name === "missing") return true;
  if (next.name === "attendance" || next.name === "history" || next.name === "mark" || next.name === "reports") return true;
  if ((next.name === "list" || next.name === "detail") && (next.resource === "classes" || next.resource === "subjects")) {
    return true;
  }
  return false;
}

function catalog() {
  if (signedIn?.role === "STUDENT" && studentLists[route.resource]) return studentLists[route.resource];
  if (signedIn?.role === "TEACHER" && teacherLists[route.resource]) return teacherLists[route.resource];
  return lists[route.resource];
}

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
  const next = {
    dashboard: "/dashboard",
    teachers: "/dashboard/teachers",
    students: "/dashboard/students",
    classes: "/dashboard/classes",
    subjects: "/dashboard/subjects",
    faces: "/dashboard/faces",
    attendance: "/dashboard/attendance",
  }[location.hash.slice(1)];
  if (!next || location.pathname !== "/dashboard") return false;
  history.replaceState(null, "", next);
  return true;
}

function parseRoute() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[0] !== "dashboard") return { name: "missing" };
  if (parts.length === 1) return { name: "home" };
  const resource = parts[1];
  if (resource === "faces") {
    if (parts.length === 2) return { name: "faces", resource };
    if (parts.length === 3 && /^\d+$/.test(parts[2])) return { name: "face", resource, id: parts[2] };
    return { name: "missing" };
  }
  if (resource === "profile" && parts.length === 2) return { name: "profile", resource };
  if (resource === "attendance") {
    if (parts.length === 2) return { name: "attendance", resource };
    if (parts.length === 3 && parts[2] === "mark") return { name: "mark", resource };
    if (parts.length === 3 && parts[2] === "history") return { name: "history", resource };
    if (parts.length === 3 && parts[2] === "percentage") return { name: "percentage", resource };
    if (parts.length === 3 && parts[2] === "calendar") return { name: "calendar", resource };
    return { name: "missing" };
  }
  if (resource === "reports" && parts.length === 2) return { name: "reports", resource };
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
  const previous = route.name;
  route = parseRoute();
  const block = accessBlock(route);
  if (block) {
    sessionStorage.setItem(flashKey, {
      student: "That page is not available for your account.",
      teacher: "That page is for an admin.",
      admin: "That page is for a teacher.",
      "student-only": "That page is for a student.",
    }[block]);
    history.replaceState(null, "", "/dashboard");
    route = parseRoute();
  }
  if (previous === "mark" && route.name !== "mark") stopScanner("", { quiet: true });
  if (previous === "face" && route.name !== "face") resetFaceCapture();
  editing = null;
  pendingDelete = null;
  hideDelete();
  showMessage("");
  markCurrent();
  setHeading(pageTitle());
  const activeId = viewFor[route.name];
  for (const id of new Set(Object.values(viewFor))) {
    document.getElementById(id).hidden = id !== activeId;
  }
  document.getElementById("class-students").hidden = true;
  const editor = document.getElementById("editor");
  if (editor) editor.hidden = route.name !== "form";
  if (route.name === "home") loadHome(ticket);
  else if (route.name === "list") loadList(ticket);
  else if (route.name === "detail") loadDetail(ticket);
  else if (route.name === "form") loadForm(ticket);
  else if (route.name === "faces") loadFaces(ticket);
  else if (route.name === "face") loadFace(ticket);
  else if (route.name === "attendance" || route.name === "history") loadAttendance(ticket);
  else if (route.name === "mark") loadMark(ticket);
  else if (route.name === "reports") loadReports(ticket);
  else if (route.name === "profile") loadProfile(ticket);
  else if (route.name === "percentage") loadPercentage(ticket);
  else if (route.name === "calendar") loadCalendar(ticket);
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
  if (route.name === "profile") return "My Profile";
  if (route.name === "faces") return "Face enrollment";
  if (route.name === "face") return "Enroll face";
  if (route.name === "attendance") return signedIn?.role === "STUDENT" ? "My Attendance" : signedIn?.role === "TEACHER" ? "Today" : "Attendance";
  if (route.name === "history") return "Attendance history";
  if (route.name === "percentage") return "Attendance %";
  if (route.name === "calendar") return "Calendar";
  if (route.name === "mark") return signedIn?.role === "TEACHER" ? "Start attendance" : "Mark attendance";
  if (route.name === "reports") return "Reports";
  const list = catalog();
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
    : route.name === "mark"
      ? "/dashboard/attendance/mark"
      : route.name === "history"
        ? "/dashboard/attendance/history"
        : route.name === "percentage"
          ? "/dashboard/attendance/percentage"
          : route.name === "calendar"
            ? "/dashboard/attendance/calendar"
            : route.name === "profile"
              ? "/dashboard/profile"
              : route.name === "reports"
          ? "/dashboard/reports"
          : route.name === "faces" || route.name === "face"
            ? "/dashboard/faces"
            : route.name === "attendance"
              ? "/dashboard/attendance"
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
    return null;
  }
  if (!response.ok) return null;
  signedIn = data;
  document.getElementById("user-name").textContent = `${data.first_name} ${data.last_name}`;
  document.getElementById("user-role").textContent = label(data.role);
  document.getElementById("user-initials").textContent = initials(data.first_name, data.last_name);
  return data;
}

function paintHello(blocked) {
  const hello = document.getElementById("dashboard-hello");
  hello.dataset.blocked = blocked ? "1" : "0";
  const welcome = signedIn ? `Welcome back, ${signedIn.first_name}.` : "Welcome back.";
  if (signedIn?.role === "TEACHER") {
    hello.textContent = `${welcome} These are your classes, subjects, and attendance.`;
    return;
  }
  if (signedIn?.role === "STUDENT") {
    hello.textContent = `${welcome} This is your profile, class, subjects, and attendance.`;
    return;
  }
  hello.textContent = blocked
    ? `${welcome} Teacher, student, class, and subject lists are open to an admin.`
    : welcome;
}

async function loadHome(ticket) {
  if (signedIn?.role === "TEACHER") {
    await loadTeacherHome(ticket);
    return;
  }
  if (signedIn?.role === "STUDENT") {
    await loadStudentHome(ticket);
    return;
  }
  const cards = document.getElementById("stat-cards");
  cards.replaceChildren();
  const [entries, attendance] = await Promise.all([
    Promise.all(
      Object.entries(lists).map(async ([key, list]) => {
        const { response, data } = await request(`${list.path}?page=1&page_size=1`);
        return [key, list.title, response.ok ? data.total : null, response.status];
      }),
    ),
    request("/attendance/today?page=1&page_size=1"),
  ]);
  if (ticket !== routeTicket) return;
  paintHello(entries.some((entry) => entry[3] === 403));
  for (const [key, title, total] of entries) addStatCard(cards, `/dashboard/${key}`, title, total);
  addStatCard(cards, "/dashboard/faces", "Face enrollment", null);
  addStatCard(cards, "/dashboard/attendance", "Attendance", attendance.response.ok ? attendance.data.total : null);
}

async function loadTeacherHome(ticket) {
  const cards = document.getElementById("stat-cards");
  cards.replaceChildren();
  const [classes, subjects, today, stats] = await Promise.all([
    request("/teacher/classes?page=1&page_size=1"),
    request("/teacher/subjects?page=1&page_size=1"),
    request("/teacher/attendance/today?page=1&page_size=1"),
    request("/teacher/attendance/statistics"),
  ]);
  if (ticket !== routeTicket) return;
  paintHello(false);
  addStatCard(cards, "/dashboard/classes", "My classes", classes.response.ok ? classes.data.total : null);
  addStatCard(cards, "/dashboard/subjects", "My subjects", subjects.response.ok ? subjects.data.total : null);
  addStatCard(cards, "/dashboard/attendance", "Today", today.response.ok ? today.data.total : null);
  addStatCard(cards, "/dashboard/reports", "Reports", stats.response.ok ? stats.data.total : null);
}

async function loadStudentHome(ticket) {
  const cards = document.getElementById("stat-cards");
  cards.replaceChildren();
  const now = new Date();
  const monthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const [profile, schoolClass, subjects, percentage, month] = await Promise.all([
    request("/student/profile"),
    request("/student/class"),
    request("/student/subjects?page=1&page_size=1"),
    request("/student/attendance/percentage"),
    request(`/student/attendance/monthly?year=${now.getFullYear()}&month=${now.getMonth() + 1}`),
  ]);
  if (ticket !== routeTicket) return;
  const hello = document.getElementById("dashboard-hello");
  const welcome = signedIn ? `Welcome back, ${signedIn.first_name}.` : "Welcome back.";
  let detail = "This is your profile, class, subjects, and attendance.";
  if (profile.response.status === 404) detail = "Your roster profile is not ready yet.";
  else if (schoolClass.response.ok) detail = `You are in ${classLabel(schoolClass.data)}.`;
  else if (schoolClass.response.status === 404) detail = "You are not enrolled in a class yet.";
  hello.dataset.blocked = "0";
  hello.textContent = `${welcome} ${detail}`;
  addStatCard(cards, "/dashboard/profile", "My Profile", profile.response.ok ? profile.data.roll_number : null);
  addStatCard(cards, "/dashboard/attendance", "My Attendance", percentage.response.ok ? percentage.data.total : null);
  addStatCard(cards, "/dashboard/attendance/percentage", "Attendance %", percentage.response.ok ? percentText(percentage.data.percentage) : null);
  addStatCard(cards, `/dashboard/attendance/calendar?month=${monthKey}`, "Calendar", month.response.ok ? percentText(month.data.percentage) : null);
  addStatCard(cards, "/dashboard/subjects", "Subjects", subjects.response.ok ? subjects.data.total : null);
}

function addStatCard(cards, href, title, total) {
  cards.append(Dashboard.statCard(title, total, href));
}

function listQuery() {
  const params = new URLSearchParams(location.search);
  return {
    page: Math.max(1, Number(params.get("page")) || 1),
    search: params.get("search") || "",
    status: params.get("status") || "",
    classId: params.get("class_id") || "",
  };
}

function listPath(resource, state) {
  const params = new URLSearchParams();
  if (state.page > 1) params.set("page", String(state.page));
  if (state.search) params.set("search", state.search);
  if (state.status && signedIn?.role !== "TEACHER") params.set("status", state.status);
  if (signedIn?.role === "TEACHER" && resource === "subjects" && state.classId) {
    params.set("class_id", state.classId);
  }
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
    status: signedIn?.role === "TEACHER" ? "" : value(form, "status"),
    classId: form.elements.class_id ? value(form, "class_id") : "",
  }));
}

function turn(step) {
  if (route.name !== "list") return;
  const state = listQuery();
  go(listPath(route.resource, { ...state, page: Math.max(1, state.page + step) }));
}

async function loadList(ticket) {
  const list = catalog();
  const state = listQuery();
  const form = document.getElementById("list-form");
  const teacher = signedIn?.role === "TEACHER";
  const student = signedIn?.role === "STUDENT";
  const readOnlyList = teacher || student;
  const teacherSubjects = teacher && route.resource === "subjects";
  form.elements.search.value = state.search;
  form.elements.status.value = readOnlyList ? "" : state.status;
  document.getElementById("status-filter").hidden = readOnlyList;
  document.getElementById("class-filter").hidden = !teacherSubjects;
  if (teacherSubjects) {
    schoolClasses = await pagedCatalog("/teacher/classes", "");
    lessonClasses = schoolClasses;
    if (ticket !== routeTicket) return;
    fillClassSelect(form.elements.class_id, state.classId, "All classes");
  }
  const params = new URLSearchParams({ page: String(state.page), page_size: String(pageSize) });
  if (state.search) params.set("search", state.search);
  if (!readOnlyList && state.status) params.set("status", state.status);
  if (teacherSubjects && state.classId) params.set("class_id", state.classId);
  renderHead(list.columns);
  const body = document.getElementById("list-body");
  body.replaceChildren(messageRow(list.columns.length, "Loading…"));
  const listRequest = request(`${list.path}?${params}`);
  const classRequest = student ? request("/student/class") : Promise.resolve(null);
  const [{ response, data }, enrolled] = await Promise.all([listRequest, classRequest]);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    body.replaceChildren(messageRow(list.columns.length, "No data to display"));
    document.getElementById("list-summary").textContent = "Showing 0 – 0 of 0";
    setPager(state.page, 0);
    showMessage(response.status === 403
      ? (student ? "You cannot open this list." : "Admin access is required for this list.")
      : errorText(data));
    return;
  }
  if (student) schoolClasses = enrolled?.response?.ok ? [enrolled.data] : [];
  await prepareSources(list);
  if (ticket !== routeTicket) return;
  renderRows(list.columns, data.items);
  const total = data.total || 0;
  document.getElementById("list-summary").textContent = Dashboard.summary(state.page, pageSize, total);
  setPager(state.page, total);
}

function setPager(page, total) {
  Dashboard.setPager(
    document.getElementById("page-prev"),
    document.getElementById("page-next"),
    page,
    pageSize,
    total,
  );
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
  Dashboard.renderRows(document.getElementById("list-body"), columns, items, "No data to display");
}

function messageRow(span, text) {
  return Dashboard.messageRow(span, text);
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
  return Dashboard.statusPill(status);
}

function initials(first, last) {
  return `${first || ""} ${last || ""}`.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase() || "?";
}

function label(role) {
  return Dashboard.label(role);
}

function field(name, labelText, options = {}) {
  return { name, label: labelText, ...options };
}

function uniqueCode(prefix) {
  const token = crypto.randomUUID().replace(/-/g, "").slice(0, 12).toUpperCase();
  return `${prefix}-${token}`;
}

async function loadDetail(ticket) {
  const list = catalog();
  document.getElementById("detail-fields").replaceChildren();
  document.getElementById("class-students").hidden = true;
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
  if (signedIn?.role === "TEACHER") {
    schoolClasses = await pagedCatalog("/teacher/classes", "");
    if (ticket !== routeTicket) return;
  }
  renderDetail(list, data);
  if (signedIn?.role === "TEACHER" && route.resource === "classes") {
    await loadClassStudents(ticket, data.class_id);
  }
}

async function loadClassStudents(ticket, classId) {
  const body = document.getElementById("student-body");
  body.replaceChildren(messageRow(3, "Loading…"));
  const { response, data } = await request(
    `/teacher/classes/${classId}/students?status=ACTIVE&page=1&page_size=100`,
  );
  if (ticket !== routeTicket) return;
  const panel = document.getElementById("class-students");
  panel.hidden = false;
  if (!response.ok) {
    body.replaceChildren(messageRow(3, "No data to display"));
    document.getElementById("student-summary").textContent = "Showing 0 – 0 of 0";
    showMessage(errorText(data));
    return;
  }
  const columns = [
    textColumn("Name", (row) => `${row.first_name} ${row.last_name}`),
    textColumn("Roll number", (row) => row.roll_number),
    { label: "Status", cell: (row) => statusCell(row.status) },
  ];
  Dashboard.renderRows(body, columns, data.items || [], "No students in this class");
  document.getElementById("student-summary").textContent = Dashboard.summary(1, 100, data.total || 0);
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
  const edit = document.getElementById("detail-edit");
  const remove = document.getElementById("detail-delete");
  if (signedIn?.role === "TEACHER") {
    remove.hidden = true;
    if (route.resource === "classes") {
      edit.textContent = "My subjects";
      edit.href = `/dashboard/subjects?class_id=${row.class_id}`;
    } else {
      edit.textContent = "Start attendance";
      edit.href = `/dashboard/attendance/mark?class_id=${row.class_id}&subject_id=${row.subject_id}`;
    }
  } else {
    remove.hidden = false;
    edit.textContent = "Edit";
    edit.href = `/dashboard/${route.resource}/${row[list.idKey]}/edit`;
  }
  document.querySelector("#detail-view .actions").hidden = false;
  setHeading(recordTitle(row));
}

function shown(item, row) {
  const raw = row[item.name];
  if (raw == null || raw === "") return "—";
  if (item.source === "teachers") return teacherName(raw) || "—";
  if (item.source === "classes" || item.name === "class_id") return className(raw) || "—";
  const match = item.options?.find(([optionValue]) => optionValue === raw);
  return match ? match[1] : String(raw);
}

function recordTitle(row) {
  if (row.first_name) return `${row.first_name} ${row.last_name}`;
  if (row.name && row.section) return `${row.name}-${row.section}`;
  if (row.name && row.code) return `${row.code} · ${row.name}`;
  if (row.name) return row.name;
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
    if (item.generated) {
      control.readOnly = true;
      control.maxLength = 50;
      control.autocomplete = "off";
      control.spellcheck = false;
      control.setAttribute("aria-readonly", "true");
      control.classList.add("is-generated");
    }
  } else {
    const choices = item.source === "teachers"
      ? teacherChoices(row, item)
      : item.source === "classes"
        ? classChoices(row, item)
        : item.options;
    for (const [optionValue, optionLabel] of choices) {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionLabel;
      control.append(option);
    }
  }
  const current = row
    ? row[item.name]
    : item.generated
      ? uniqueCode(item.generated)
      : item.name === "status"
        ? "ACTIVE"
        : "";
  control.value = current == null ? "" : String(current);
  wrap.append(control);
  if (item.generated) {
    const hint = document.createElement("small");
    hint.textContent = "Assigned automatically.";
    wrap.append(hint);
  } else if (item.type === "password") {
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
    if (item.generated) {
      if (!row) body[item.name] = raw || uniqueCode(item.generated);
      continue;
    }
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

function teacherChoices(row, item) {
  const choices = [["", item?.required ? "Choose a teacher" : "Not assigned"]];
  const seen = new Set();
  for (const teacher of teachers) {
    seen.add(teacher.teacher_id);
    choices.push([String(teacher.teacher_id), `${teacher.teacher_id} · ${teacher.first_name} ${teacher.last_name}`]);
  }
  const current = item?.name === "teacher_id" ? row?.teacher_id : row?.class_teacher_id;
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

function classChoices(row, item) {
  const choices = [["", item?.required ? "Choose a class" : "Not assigned"]];
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

function facePath(state) {
  const params = new URLSearchParams();
  if (state.page > 1) params.set("page", String(state.page));
  if (state.search) params.set("search", state.search);
  const query = params.toString();
  return `/dashboard/faces${query ? `?${query}` : ""}`;
}

function turnFace(step) {
  if (route.name !== "faces") return;
  const state = listQuery();
  go(facePath({ ...state, page: Math.max(1, state.page + step) }));
}

async function loadFaces(ticket) {
  const state = listQuery();
  const form = document.getElementById("face-list-form");
  form.elements.search.value = state.search;
  const params = new URLSearchParams({ page: String(state.page), page_size: String(pageSize) });
  if (state.search) params.set("search", state.search);
  const body = document.getElementById("face-body");
  body.replaceChildren(messageRow(5, "Loading…"));
  await refreshClasses();
  const { response, data } = await request(`/admin/students?${params}`);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    body.replaceChildren(messageRow(5, "No data to display"));
    document.getElementById("face-summary").textContent = "Showing 0 – 0 of 0";
    setNamedPager("face", state.page, 0);
    showMessage(response.status === 403 ? "Admin access is required for face enrollment." : errorText(data));
    return;
  }
  const rows = await Promise.all((data.items || []).map(async (student) => {
    const face = await request(`/students/${student.student_id}/face`);
    return { student, face: face.response.ok ? face.data : null };
  }));
  if (ticket !== routeTicket) return;
  if (!rows.length) {
    body.replaceChildren(messageRow(5, "No data to display"));
  } else {
    body.replaceChildren(...rows.map(faceRow));
  }
  const total = data.total || 0;
  const start = total === 0 ? 0 : (state.page - 1) * pageSize + 1;
  const end = Math.min(state.page * pageSize, total);
  document.getElementById("face-summary").textContent = `Showing ${start} – ${end} of ${total}`;
  setNamedPager("face", state.page, total);
}

function faceRow({ student, face }) {
  const row = document.createElement("tr");
  const name = document.createElement("td");
  const link = document.createElement("a");
  link.href = `/dashboard/faces/${student.student_id}`;
  link.textContent = `${student.first_name} ${student.last_name}`;
  name.append(link);
  const roll = document.createElement("td");
  roll.textContent = student.roll_number || "—";
  const schoolClass = document.createElement("td");
  schoolClass.textContent = className(student.class_id) || "—";
  const status = document.createElement("td");
  if (face) {
    const pill = statusPill(face.status);
    if (face.status === "ENROLLED") pill.textContent = `Enrolled · ${face.sample_count}`;
    status.append(pill);
  } else {
    status.textContent = "—";
  }
  const action = document.createElement("td");
  action.className = "row-actions";
  const enroll = document.createElement("a");
  enroll.className = "text-button";
  enroll.href = `/dashboard/faces/${student.student_id}`;
  enroll.textContent = face?.status === "ENROLLED" ? "Update" : "Enroll";
  action.append(enroll);
  row.append(name, roll, schoolClass, status, action);
  return row;
}

async function loadFace(ticket) {
  const fields = document.getElementById("face-fields");
  fields.replaceChildren();
  resetFaceCapture();
  document.getElementById("face-form").hidden = true;
  const [studentResult, faceResult] = await Promise.all([
    request(`/admin/students/${route.id}`),
    request(`/students/${route.id}/face`),
  ]);
  if (ticket !== routeTicket) return;
  if (!studentResult.response.ok) {
    showMessage(studentResult.response.status === 403 ? "Admin access is required." : errorText(studentResult.data));
    return;
  }
  const student = studentResult.data;
  const face = faceResult.response.ok ? faceResult.data : null;
  setHeading(`${student.first_name} ${student.last_name}`);
  const blocks = [
    ["Student", `${student.first_name} ${student.last_name}`],
    ["Roll number", student.roll_number || "—"],
    ["Samples", face ? String(face.sample_count) : "—"],
  ];
  fields.replaceChildren(...blocks.map(([caption, text]) => definition(caption, text)));
  const statusWrap = definition("Face", "");
  statusWrap.querySelector("dd").replaceChildren(statusPill(face?.status || "NOT_ENROLLED"));
  fields.append(statusWrap);
  const add = document.getElementById("face-add");
  const full = Boolean(face && face.sample_count >= 5);
  faceAddLocked = full;
  add.disabled = full;
  document.getElementById("face-hint").textContent = full
    ? "This student already has 5 samples. Replace them to store a new image."
    : "One clear photo. The image is checked and not stored.";
  document.getElementById("face-form").hidden = false;
}

function definition(caption, text) {
  const wrap = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = caption;
  const valueNode = document.createElement("dd");
  valueNode.textContent = text;
  wrap.append(term, valueNode);
  return wrap;
}

function setFaceSource(source) {
  const next = source === "camera" ? "camera" : "upload";
  const showCamera = next === "camera";
  if (!showCamera && faceCameraStream) {
    stopFaceCamera();
    if (!faceCaptureBlob) syncFaceCameraControls("idle");
  }
  faceSource = next;
  document.getElementById("face-upload").hidden = showCamera;
  document.getElementById("face-camera").hidden = !showCamera;
  const uploadBtn = document.getElementById("face-source-upload");
  const cameraBtn = document.getElementById("face-source-camera");
  uploadBtn.className = showCamera ? "button-quiet" : "button";
  cameraBtn.className = showCamera ? "button" : "button-quiet";
  uploadBtn.setAttribute("aria-pressed", String(!showCamera));
  cameraBtn.setAttribute("aria-pressed", String(showCamera));
  if (showCamera) syncFaceCameraControls(faceCaptureBlob ? "captured" : faceCameraStream ? "live" : "idle");
}

function resetFaceCapture() {
  stopFaceCamera();
  clearFacePreview();
  faceAddLocked = false;
  const form = document.getElementById("face-form");
  if (form?.elements.image) form.elements.image.value = "";
  setFaceCameraStatus("Start the camera, then capture one clear photo.");
  syncFaceCameraControls("idle");
  setFaceSource("upload");
}

function clearFacePreview() {
  faceCaptureBlob = null;
  if (facePreviewUrl) {
    URL.revokeObjectURL(facePreviewUrl);
    facePreviewUrl = "";
  }
  const preview = document.getElementById("face-preview");
  if (preview) preview.removeAttribute("src");
}

async function startFaceCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showMessage("This browser does not support webcam capture.");
    return;
  }
  stopFaceCamera();
  clearFacePreview();
  const start = document.getElementById("face-camera-start");
  if (start) start.disabled = true;
  try {
    faceCameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 720 } },
      audio: false,
    });
    if (faceSource !== "camera" || route.name !== "face") {
      stopFaceCamera();
      return;
    }
    const video = document.getElementById("face-video");
    video.srcObject = faceCameraStream;
    await video.play();
    syncFaceCameraControls("live");
    setFaceCameraStatus("Look at the camera, then capture one clear photo.");
  } catch (error) {
    stopFaceCamera();
    syncFaceCameraControls("idle");
    showMessage(error?.name === "NotAllowedError" ? "Camera permission was blocked." : "Could not start the camera.");
  }
}

function stopFaceCamera() {
  if (faceCameraStream) {
    for (const track of faceCameraStream.getTracks()) track.stop();
    faceCameraStream = null;
  }
  const video = document.getElementById("face-video");
  if (video) video.srcObject = null;
}

async function captureFacePhoto() {
  const video = document.getElementById("face-video");
  if (!video?.videoWidth || !video.videoHeight) {
    setFaceCameraStatus("Waiting for the camera image...");
    return;
  }
  const canvas = document.getElementById("face-canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob || faceSource !== "camera" || route.name !== "face") return;
  if (facePreviewUrl) URL.revokeObjectURL(facePreviewUrl);
  faceCaptureBlob = blob;
  facePreviewUrl = URL.createObjectURL(blob);
  document.getElementById("face-preview").src = facePreviewUrl;
  stopFaceCamera();
  syncFaceCameraControls("captured");
  setFaceCameraStatus("Photo captured. Add it as a sample, or retake.");
}

function retakeFacePhoto() {
  clearFacePreview();
  syncFaceCameraControls("idle");
  startFaceCamera();
}

function syncFaceCameraControls(state) {
  faceCameraState = state;
  const start = document.getElementById("face-camera-start");
  const snap = document.getElementById("face-camera-snap");
  const retake = document.getElementById("face-camera-retake");
  const video = document.getElementById("face-video");
  const preview = document.getElementById("face-preview");
  if (!start || !snap || !retake) return;
  start.hidden = state !== "idle";
  start.disabled = state !== "idle";
  snap.hidden = state !== "live";
  snap.disabled = state !== "live";
  retake.hidden = state !== "captured";
  retake.disabled = state !== "captured";
  if (video) video.hidden = state === "captured";
  if (preview) preview.hidden = state !== "captured";
}

function setFaceCameraStatus(text) {
  const status = document.getElementById("face-camera-status");
  if (status) status.textContent = text;
}

function setFaceBusy(busy) {
  const ids = [
    "face-add",
    "face-replace",
    "face-cancel",
    "face-source-upload",
    "face-source-camera",
    "face-camera-start",
    "face-camera-snap",
    "face-camera-retake",
  ];
  if (busy) {
    for (const id of ids) {
      const button = document.getElementById(id);
      if (button) button.disabled = true;
    }
    return;
  }
  document.getElementById("face-add").disabled = faceAddLocked;
  document.getElementById("face-replace").disabled = false;
  document.getElementById("face-cancel").disabled = false;
  document.getElementById("face-source-upload").disabled = false;
  document.getElementById("face-source-camera").disabled = false;
  syncFaceCameraControls(faceCameraState);
}

async function saveFace(method) {
  if (route.name !== "face") return;
  const form = document.getElementById("face-form");
  const file = faceSource === "camera"
    ? (faceCaptureBlob ? new File([faceCaptureBlob], "webcam-face.jpg", { type: "image/jpeg" }) : null)
    : form.elements.image.files?.[0];
  if (!file) {
    showMessage(faceSource === "camera" ? "Capture a photo from the webcam." : "Choose an image.");
    return;
  }
  const body = new FormData();
  body.append("image", file);
  setFaceBusy(true);
  let failed = false;
  try {
    const { response, data } = await requestForm(`/students/${route.id}/face`, body, method);
    if (!response.ok) {
      showMessage(errorText(data));
      failed = true;
      return;
    }
    sessionStorage.setItem(flashKey, method === "PUT" ? "Face samples replaced." : "Face sample added.");
    showRoute();
  } catch {
    failed = true;
    showMessage("The server could not be reached.");
  } finally {
    if (failed && route.name === "face") setFaceBusy(false);
  }
}

function attendanceQuery() {
  const params = new URLSearchParams(location.search);
  return {
    page: Math.max(1, Number(params.get("page")) || 1),
    classId: params.get("class_id") || "",
    subjectId: params.get("subject_id") || "",
    dateFrom: params.get("date_from") || "",
    dateTo: params.get("date_to") || "",
  };
}

function attendancePath(state) {
  const student = signedIn?.role === "STUDENT";
  const showDates = student || route.name === "history";
  const params = new URLSearchParams();
  if (state.page > 1) params.set("page", String(state.page));
  if (!student && state.classId) params.set("class_id", state.classId);
  if (state.subjectId) params.set("subject_id", state.subjectId);
  if (showDates && state.dateFrom) params.set("date_from", state.dateFrom);
  if (showDates && state.dateTo) params.set("date_to", state.dateTo);
  const query = params.toString();
  const base = route.name === "history" ? "/dashboard/attendance/history" : "/dashboard/attendance";
  return `${base}${query ? `?${query}` : ""}`;
}

function moveAttendance() {
  if (route.name !== "attendance" && route.name !== "history") return;
  const form = document.getElementById("attendance-form");
  const student = signedIn?.role === "STUDENT";
  const classId = student ? "" : value(form, "class_id");
  let subjectId = value(form, "subject_id");
  if (subjectId && !subjectChoices(classId).some(([id]) => id === subjectId)) subjectId = "";
  go(attendancePath({
    page: 1,
    classId,
    subjectId,
    dateFrom: value(form, "date_from"),
    dateTo: value(form, "date_to"),
  }));
}

function turnAttendance(step) {
  if (route.name !== "attendance" && route.name !== "history") return;
  const state = attendanceQuery();
  go(attendancePath({ ...state, page: Math.max(1, state.page + step) }));
}

async function loadAttendance(ticket) {
  const state = attendanceQuery();
  const student = signedIn?.role === "STUDENT";
  const columns = student ? 5 : 8;
  const body = document.getElementById("attendance-body");
  setAttendanceHead(student);
  body.replaceChildren(messageRow(columns, "Loading…"));
  const showDates = student || route.name === "history";
  document.querySelectorAll(".history-only").forEach((node) => {
    node.hidden = !showDates;
  });
  document.getElementById("attendance-class-filter").hidden = student;
  await loadLessonCatalogs();
  if (ticket !== routeTicket) return;
  const form = document.getElementById("attendance-form");
  fillClassSelect(form.elements.class_id, student ? "" : state.classId, "All");
  fillSubjectSelect(form.elements.subject_id, student ? "" : state.classId, state.subjectId, "All");
  form.elements.date_from.value = state.dateFrom;
  form.elements.date_to.value = state.dateTo;
  const params = new URLSearchParams({ page: String(state.page), page_size: String(pageSize) });
  if (!student && state.classId) params.set("class_id", state.classId);
  if (state.subjectId) params.set("subject_id", state.subjectId);
  if (showDates && state.dateFrom) params.set("date_from", state.dateFrom);
  if (showDates && state.dateTo) params.set("date_to", state.dateTo);
  const endpoint = student
    ? "/student/attendance"
    : route.name === "history"
      ? "/teacher/attendance"
      : signedIn?.role === "TEACHER"
        ? "/teacher/attendance/today"
        : "/attendance/today";
  const { response, data } = await request(`${endpoint}?${params}`);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    body.replaceChildren(messageRow(columns, "No data to display"));
    document.getElementById("attendance-summary").textContent = "Showing 0 – 0 of 0";
    setNamedPager("attendance", state.page, 0);
    showMessage(errorText(data));
    return;
  }
  const items = data.items || [];
  body.replaceChildren(...(items.length ? items.map(attendanceRow) : [messageRow(columns, "No data to display")]));
  const total = data.total || 0;
  document.getElementById("attendance-summary").textContent = Dashboard.summary(state.page, pageSize, total);
  setNamedPager("attendance", state.page, total);
}

function setAttendanceHead(student) {
  const labels = student
    ? ["Date", "Subject", "Status", "Check-in", "Method"]
    : ["Student", "Roll number", "Class", "Subject", "Status", "Check-in", "Method", "Confidence"];
  const row = document.createElement("tr");
  for (const label of labels) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    row.append(cell);
  }
  document.getElementById("attendance-head").replaceChildren(row);
}

function attendanceRow(row) {
  if (signedIn?.role === "STUDENT") return studentAttendanceRow(row);
  const line = document.createElement("tr");
  const values = [
    `${row.first_name} ${row.last_name}`,
    row.roll_number,
    lessonClassName(row.class_id),
    lessonSubjectName(row.subject_id),
  ];
  for (const text of values) {
    const cell = document.createElement("td");
    cell.textContent = text || "—";
    line.append(cell);
  }
  const status = document.createElement("td");
  status.append(statusPill(row.status));
  const when = document.createElement("td");
  when.textContent = clock(row.check_in_time);
  const method = document.createElement("td");
  method.textContent = row.recognition_method === "FACE_RECOGNITION" ? "Face recognition" : "Manual";
  const confidence = document.createElement("td");
  confidence.textContent = row.confidence_score == null ? "—" : Number(row.confidence_score).toFixed(2);
  line.append(status, when, method, confidence);
  return line;
}

function studentAttendanceRow(row) {
  const line = document.createElement("tr");
  const dateCell = document.createElement("td");
  dateCell.textContent = dayLabel(row.date);
  const subject = document.createElement("td");
  subject.textContent = lessonSubjectName(row.subject_id);
  const status = document.createElement("td");
  status.append(statusPill(row.status));
  const when = document.createElement("td");
  when.textContent = clock(row.check_in_time);
  const method = document.createElement("td");
  method.textContent = row.recognition_method === "FACE_RECOGNITION" ? "Face recognition" : "Manual";
  line.append(dateCell, subject, status, when, method);
  return line;
}

async function loadMark(ticket) {
  stopScanner("", { quiet: true });
  await loadLessonCatalogs();
  if (ticket !== routeTicket) return;
  const params = new URLSearchParams(location.search);
  const classId = params.get("class_id") || "";
  const subjectId = params.get("subject_id") || "";
  const camera = document.getElementById("camera-form");
  const manual = document.getElementById("manual-form");
  fillClassSelect(camera.elements.class_id, classId, "Choose a class");
  fillSubjectSelect(camera.elements.subject_id, classId, subjectId, "Choose a subject");
  fillClassSelect(manual.elements.class_id, classId, "Choose a class");
  fillSubjectSelect(manual.elements.subject_id, classId, subjectId, "Choose a subject");
  fillStudentSelect(manual.elements.student_id, classId);
  manual.hidden = lessonStudents.length === 0;
  setScannerStatus(
    signedIn?.role === "TEACHER"
      ? "Choose a class and subject, then start the attendance session."
      : "Choose a class and subject, then start scanning.",
  );
}

async function startScanner() {
  const form = document.getElementById("camera-form");
  const classId = value(form, "class_id");
  const subjectId = value(form, "subject_id");
  if (!classId || !subjectId) {
    showMessage("Choose a class and a subject.");
    return;
  }
  if (signedIn?.role === "TEACHER") {
    const started = await request("/teacher/attendance/sessions", {
      class_id: Number(classId),
      subject_id: Number(subjectId),
    });
    if (!started.response.ok) {
      showMessage(errorText(started.data));
      return;
    }
    openSessionId = started.data.session_id;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    closeOpenSession();
    showMessage("This browser does not support webcam scanning.");
    return;
  }
  releaseScanner("", { quiet: true });
  try {
    scannerStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 720 } },
      audio: false,
    });
    const video = document.getElementById("scanner-video");
    video.srcObject = scannerStream;
    await video.play();
    setScannerControls(true);
    setScannerStatus("Scanning... keep one enrolled face clearly in the camera.");
    await scanCurrentFrame();
    scannerTimer = window.setInterval(scanCurrentFrame, scannerIntervalMs);
  } catch (error) {
    stopScanner("", { quiet: true });
    showMessage(error?.name === "NotAllowedError" ? "Camera permission was blocked." : "Could not start the camera.");
  }
}

function closeOpenSession() {
  const id = openSessionId;
  openSessionId = null;
  if (!id) return;
  request(`/teacher/attendance/sessions/${id}/close`, null, "POST").catch(() => {});
}

function stopScanner(message, options = {}) {
  closeOpenSession();
  releaseScanner(message, options);
}

function releaseScanner(message, options = {}) {
  if (scannerTimer) {
    window.clearInterval(scannerTimer);
    scannerTimer = null;
  }
  if (scannerStream) {
    for (const track of scannerStream.getTracks()) track.stop();
    scannerStream = null;
  }
  scannerBusy = false;
  const video = document.getElementById("scanner-video");
  if (video) video.srcObject = null;
  setScannerControls(false);
  if (!options.quiet && message) setScannerStatus(message);
}

async function scanCurrentFrame() {
  if (scannerBusy || !scannerStream) return;
  const form = document.getElementById("camera-form");
  const classId = value(form, "class_id");
  const subjectId = value(form, "subject_id");
  if (!classId || !subjectId) {
    stopScanner("Choose a class and subject before scanning.");
    return;
  }
  scannerBusy = true;
  try {
    const blob = await captureScannerFrame();
    if (!blob) {
      setScannerStatus("Waiting for the camera image...");
      return;
    }
    const body = new FormData();
    body.append("class_id", classId);
    body.append("subject_id", subjectId);
    body.append("image", blob, "webcam-frame.jpg");
    const { response, data } = await requestForm("/attendance/mark", body, "POST");
    if (response.ok) {
      const name = `${data.first_name} ${data.last_name}`.trim();
      if (signedIn?.role === "TEACHER") {
        setScannerStatus(
          data.created
            ? `${name} marked ${label(data.status).toLowerCase()}.`
            : `${name} was already marked today.`,
        );
        return;
      }
      stopScanner("Attendance marked.");
      sessionStorage.setItem(flashKey, data.created ? "Attendance marked from live camera." : "Already marked today.");
      go("/dashboard/attendance");
      return;
    }
    if (response.status === 422) {
      setScannerStatus("No enrolled face matched yet. Keep one face centered and well lit.");
      return;
    }
    const text = errorText(data);
    stopScanner(text);
    showMessage(text);
  } catch {
    stopScanner("Scanner stopped because the server could not be reached.");
    showMessage("The server could not be reached.");
  } finally {
    scannerBusy = false;
  }
}

function captureScannerFrame() {
  const video = document.getElementById("scanner-video");
  if (!video.videoWidth || !video.videoHeight) return Promise.resolve(null);
  const canvas = document.getElementById("scanner-canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
}

function setScannerControls(active) {
  const start = document.getElementById("scanner-start");
  const stop = document.getElementById("scanner-stop");
  if (!start || !stop) return;
  start.disabled = active;
  stop.disabled = !active;
}

function setScannerStatus(text) {
  const status = document.getElementById("scanner-status");
  if (status) status.textContent = text;
}

async function saveManualMark(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const studentId = value(form, "student_id");
  const classId = value(form, "class_id");
  const subjectId = value(form, "subject_id");
  if (!studentId || !classId || !subjectId) {
    showMessage("Choose a student, class, and subject.");
    return;
  }
  await submitMark(form, () => request("/attendance/manual", {
    student_id: Number(studentId),
    class_id: Number(classId),
    subject_id: Number(subjectId),
    status: value(form, "status"),
  }));
}

async function submitMark(form, send) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const { response, data } = await send();
    if (!response.ok) {
      showMessage(errorText(data));
      return;
    }
    sessionStorage.setItem(flashKey, data.created ? "Attendance marked." : "Already marked today.");
    go("/dashboard/attendance");
  } catch {
    showMessage("The server could not be reached.");
  } finally {
    button.disabled = false;
  }
}

async function loadLessonCatalogs() {
  if (signedIn?.role === "STUDENT") {
    const [schoolClass, breakdown] = await Promise.all([
      request("/student/class"),
      request("/student/attendance/by-subject"),
    ]);
    lessonClasses = schoolClass.response.ok ? [schoolClass.data] : [];
    lessonSubjects = (breakdown.response.ok ? breakdown.data.items : []).map((item) => ({
      subject_id: item.subject_id,
      class_id: item.class_id,
      name: item.name,
      code: item.code,
    }));
    lessonStudents = [];
    return;
  }
  const teacher = signedIn?.role === "TEACHER";
  const [classes, subjects] = await Promise.all([
    pagedCatalog(teacher ? "/teacher/classes" : "/admin/classes", teacher ? "" : "/teacher/classes"),
    pagedCatalog(teacher ? "/teacher/subjects" : "/admin/subjects", teacher ? "" : "/teacher/subjects"),
  ]);
  lessonClasses = classes;
  lessonSubjects = subjects;
  lessonStudents = teacher
    ? await studentsForClasses(lessonClasses)
    : await pagedCatalog("/admin/students", "");
  lessonClasses.sort((left, right) => classLabel(left).localeCompare(classLabel(right)));
  lessonSubjects.sort((left, right) => String(left.name).localeCompare(String(right.name)));
  lessonStudents.sort((left, right) =>
    `${left.first_name} ${left.last_name}`.localeCompare(`${right.first_name} ${right.last_name}`),
  );
}

async function pagedCatalog(adminPath, teacherPath) {
  const rows = [];
  let path = adminPath;
  let pageNumber = 1;
  while (pageNumber <= 20) {
    const { response, data } = await request(`${path}?page=${pageNumber}&page_size=100`);
    if (response.status === 403 && teacherPath && path === adminPath) {
      path = teacherPath;
      pageNumber = 1;
      rows.length = 0;
      continue;
    }
    if (!response.ok) return rows;
    rows.push(...(data.items || []));
    if (!data.items?.length || rows.length >= (data.total || 0)) break;
    pageNumber += 1;
  }
  return rows;
}

function fillClassSelect(select, current, blank) {
  fillSelect(select, lessonClasses.map((schoolClass) => [String(schoolClass.class_id), classLabel(schoolClass)]), current, blank);
}

function fillSubjectSelect(select, classId, current, blank) {
  fillSelect(select, subjectChoices(classId), current, blank);
}

async function studentsForClasses(classes) {
  const rows = [];
  for (const schoolClass of classes) {
    let pageNumber = 1;
    let seen = 0;
    while (pageNumber <= 20) {
      const { response, data } = await request(
        `/teacher/classes/${schoolClass.class_id}/students?status=ACTIVE&page=${pageNumber}&page_size=100`,
      );
      if (!response.ok) break;
      const items = data.items || [];
      rows.push(...items.map((item) => ({ ...item, class_id: schoolClass.class_id })));
      seen += items.length;
      if (!items.length || seen >= (data.total || 0)) break;
      pageNumber += 1;
    }
  }
  return rows;
}

function fillStudentSelect(select, classId) {
  const pool = classId
    ? lessonStudents.filter((student) => String(student.class_id) === String(classId))
    : lessonStudents;
  fillSelect(
    select,
    pool.map((student) => [
      String(student.student_id),
      `${student.roll_number} · ${student.first_name} ${student.last_name}`,
    ]),
    "",
    pool.length ? "Choose a student" : "No students in this class",
  );
}

function subjectChoices(classId) {
  return lessonSubjects
    .filter((subject) => subject.status !== "INACTIVE")
    .filter((subject) => !classId || String(subject.class_id) === String(classId))
    .map((subject) => [String(subject.subject_id), `${subject.code} · ${subject.name}`]);
}

function fillSelect(select, choices, current, blank) {
  Dashboard.fillSelect(select, choices, current, blank);
}

function reportQuery() {
  const params = new URLSearchParams(location.search);
  return {
    classId: params.get("class_id") || "",
    subjectId: params.get("subject_id") || "",
    dateFrom: params.get("date_from") || "",
    dateTo: params.get("date_to") || "",
  };
}

function reportPath(state) {
  const params = new URLSearchParams();
  if (state.classId) params.set("class_id", state.classId);
  if (state.subjectId) params.set("subject_id", state.subjectId);
  if (state.dateFrom) params.set("date_from", state.dateFrom);
  if (state.dateTo) params.set("date_to", state.dateTo);
  const query = params.toString();
  return `/dashboard/reports${query ? `?${query}` : ""}`;
}

function moveReport() {
  if (route.name !== "reports") return;
  const form = document.getElementById("report-form");
  const classId = value(form, "class_id");
  let subjectId = value(form, "subject_id");
  if (subjectId && !subjectChoices(classId).some(([id]) => id === subjectId)) subjectId = "";
  go(reportPath({
    classId,
    subjectId,
    dateFrom: value(form, "date_from"),
    dateTo: value(form, "date_to"),
  }));
}

async function loadReports(ticket) {
  const state = reportQuery();
  const cards = document.getElementById("report-cards");
  cards.replaceChildren();
  await loadLessonCatalogs();
  if (ticket !== routeTicket) return;
  const form = document.getElementById("report-form");
  fillClassSelect(form.elements.class_id, state.classId, "All");
  fillSubjectSelect(form.elements.subject_id, state.classId, state.subjectId, "All");
  form.elements.date_from.value = state.dateFrom;
  form.elements.date_to.value = state.dateTo;
  const params = new URLSearchParams();
  if (state.classId) params.set("class_id", state.classId);
  if (state.subjectId) params.set("subject_id", state.subjectId);
  if (state.dateFrom) params.set("date_from", state.dateFrom);
  if (state.dateTo) params.set("date_to", state.dateTo);
  const query = params.toString();
  const { response, data } = await request(`/teacher/attendance/statistics${query ? `?${query}` : ""}`);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    showMessage(errorText(data));
    return;
  }
  cards.append(
    Dashboard.statCard("Present", data.present),
    Dashboard.statCard("Absent", data.absent),
    Dashboard.statCard("Late", data.late),
    Dashboard.statCard("Excused", data.excused),
    Dashboard.statCard("Total", data.total),
  );
}

async function exportReport() {
  const form = document.getElementById("report-form");
  const params = new URLSearchParams();
  const classId = value(form, "class_id");
  const subjectId = value(form, "subject_id");
  const dateFrom = value(form, "date_from");
  const dateTo = value(form, "date_to");
  if (classId) params.set("class_id", classId);
  if (subjectId) params.set("subject_id", subjectId);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const token = sessionStorage.getItem(tokenKey);
  try {
    const response = await fetch(`/teacher/attendance/export?${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      showMessage(errorText(data));
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "attendance-report.csv";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch {
    showMessage("The server could not be reached.");
  }
}

function lessonClassName(id) {
  const schoolClass = lessonClasses.find((item) => item.class_id === id);
  return schoolClass ? classLabel(schoolClass) : className(id);
}

function lessonSubjectName(id) {
  const subject = lessonSubjects.find((item) => item.subject_id === id);
  return subject ? subject.name : `Subject ${id}`;
}

function clock(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function dayLabel(value) {
  if (!value) return "—";
  const parts = String(value).slice(0, 10).split("-").map(Number);
  if (parts.length !== 3 || parts.some((part) => Number.isNaN(part))) return String(value);
  return new Date(parts[0], parts[1] - 1, parts[2]).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function genderLabel(value) {
  return { FEMALE: "Female", MALE: "Male", OTHER: "Other" }[value] || "—";
}

function percentText(value) {
  if (value == null || value === "") return "—";
  return `${Number(value).toFixed(1)}%`;
}

async function loadProfile(ticket) {
  const fields = document.getElementById("profile-fields");
  fields.replaceChildren();
  const [profile, schoolClass] = await Promise.all([
    request("/student/profile"),
    request("/student/class"),
  ]);
  if (ticket !== routeTicket) return;
  if (!profile.response.ok) {
    showMessage(profile.response.status === 404 ? "Student profile not found." : errorText(profile.data));
    return;
  }
  const row = profile.data;
  const enrolled = schoolClass.response.ok ? schoolClass.data : null;
  const pairs = [
    ["Name", `${row.first_name} ${row.last_name}`],
    ["Email", row.email],
    ["Phone", row.phone || "—"],
    ["Roll number", row.roll_number],
    ["Date of birth", row.date_of_birth ? dayLabel(row.date_of_birth) : "—"],
    ["Gender", genderLabel(row.gender)],
    ["Class", enrolled ? `${enrolled.name}-${enrolled.section}` : "Not enrolled"],
    ["Academic year", enrolled ? enrolled.academic_year : "—"],
  ];
  fields.replaceChildren(...pairs.map(([term, value]) => {
    const wrap = document.createElement("div");
    const labelNode = document.createElement("dt");
    labelNode.textContent = term;
    const valueNode = document.createElement("dd");
    valueNode.textContent = value || "—";
    wrap.append(labelNode, valueNode);
    return wrap;
  }));
  const statusWrap = document.createElement("div");
  const statusTerm = document.createElement("dt");
  statusTerm.textContent = "Status";
  const statusValue = document.createElement("dd");
  statusValue.append(statusPill(row.status));
  statusWrap.append(statusTerm, statusValue);
  fields.append(statusWrap);
  setHeading(`${row.first_name} ${row.last_name}`);
}

async function loadPercentage(ticket) {
  const cards = document.getElementById("percentage-cards");
  const body = document.getElementById("percentage-body");
  cards.replaceChildren();
  body.replaceChildren(messageRow(7, "Loading…"));
  const [overall, subjects] = await Promise.all([
    request("/student/attendance/percentage"),
    request("/student/attendance/by-subject"),
  ]);
  if (ticket !== routeTicket) return;
  if (!overall.response.ok) {
    body.replaceChildren(messageRow(7, "No data to display"));
    showMessage(errorText(overall.data));
    return;
  }
  const counts = overall.data;
  addStatCard(cards, "", "Attendance %", percentText(counts.percentage));
  addStatCard(cards, "", "Present", counts.present);
  addStatCard(cards, "", "Absent", counts.absent);
  addStatCard(cards, "", "Late", counts.late);
  addStatCard(cards, "", "Excused", counts.excused);
  const columns = [
    textColumn("Subject", (row) => row.name),
    textColumn("Code", (row) => row.code),
    textColumn("Present", (row) => String(row.present)),
    textColumn("Absent", (row) => String(row.absent)),
    textColumn("Late", (row) => String(row.late)),
    textColumn("Excused", (row) => String(row.excused)),
    textColumn("Attendance %", (row) => percentText(row.percentage)),
  ];
  if (!subjects.response.ok) {
    body.replaceChildren(messageRow(7, "No data to display"));
    showMessage(errorText(subjects.data));
    return;
  }
  Dashboard.renderRows(body, columns, subjects.data.items || [], "No subjects to display");
}

function calendarQuery() {
  const params = new URLSearchParams(location.search);
  const now = new Date();
  const fallback = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const month = /^\d{4}-\d{2}$/.test(params.get("month") || "") ? params.get("month") : fallback;
  return { month, subjectId: params.get("subject_id") || "" };
}

function moveCalendar() {
  if (route.name !== "calendar") return;
  const form = document.getElementById("calendar-form");
  const params = new URLSearchParams();
  const month = value(form, "month");
  const subjectId = value(form, "subject_id");
  if (month) params.set("month", month);
  if (subjectId) params.set("subject_id", subjectId);
  const query = params.toString();
  go(`/dashboard/attendance/calendar${query ? `?${query}` : ""}`);
}

async function loadCalendar(ticket) {
  const state = calendarQuery();
  const form = document.getElementById("calendar-form");
  const cards = document.getElementById("calendar-cards");
  cards.replaceChildren();
  document.getElementById("calendar-grid").replaceChildren();
  form.elements.month.value = state.month;
  await loadLessonCatalogs();
  if (ticket !== routeTicket) return;
  fillSubjectSelect(form.elements.subject_id, "", state.subjectId, "All");
  const [year, month] = state.month.split("-").map(Number);
  const params = new URLSearchParams({ year: String(year), month: String(month) });
  if (state.subjectId) params.set("subject_id", state.subjectId);
  const { response, data } = await request(`/student/attendance/calendar?${params}`);
  if (ticket !== routeTicket) return;
  if (!response.ok) {
    document.getElementById("calendar-summary").textContent = "Attendance for this month could not be loaded.";
    showMessage(errorText(data));
    return;
  }
  document.getElementById("calendar-summary").textContent = data.percentage == null
    ? "No counted attendance this month."
    : `${percentText(data.percentage)} attended this month.`;
  addStatCard(cards, "", "Attendance %", percentText(data.percentage));
  addStatCard(cards, "", "Present", data.present);
  addStatCard(cards, "", "Absent", data.absent);
  addStatCard(cards, "", "Late", data.late);
  renderCalendar(year, month, data.days || []);
}

function renderCalendar(year, month, days) {
  const grid = document.getElementById("calendar-grid");
  const byDate = new Map(days.map((day) => [day.date, day]));
  const nodes = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((name) => {
    const cell = document.createElement("div");
    cell.className = "calendar-head";
    cell.textContent = name;
    return cell;
  });
  const offset = (new Date(year, month - 1, 1).getDay() + 6) % 7;
  const count = new Date(year, month, 0).getDate();
  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  for (let index = 0; index < offset; index += 1) {
    const pad = document.createElement("div");
    pad.className = "calendar-day is-pad";
    pad.setAttribute("aria-hidden", "true");
    nodes.push(pad);
  }
  for (let day = 1; day <= count; day += 1) {
    const key = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const cell = document.createElement("div");
    const mark = byDate.get(key);
    cell.className = "calendar-day";
    if (mark) cell.classList.add(`is-${String(mark.status).toLowerCase()}`);
    if (key === todayKey) cell.classList.add("is-today");
    const number = document.createElement("strong");
    number.textContent = String(day);
    cell.append(number);
    if (mark) {
      const note = document.createElement("small");
      note.textContent = label(mark.status);
      cell.append(note);
      cell.setAttribute("aria-label", `${dayLabel(key)}, ${note.textContent}`);
    }
    nodes.push(cell);
  }
  grid.replaceChildren(...nodes);
}

function setNamedPager(prefix, page, total) {
  Dashboard.setPager(
    document.getElementById(`${prefix}-prev`),
    document.getElementById(`${prefix}-next`),
    page,
    pageSize,
    total,
  );
}

async function requestForm(path, body, method) {
  const headers = {};
  const token = sessionStorage.getItem(tokenKey);
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { method, headers, body });
  const data = await response.json().catch(() => ({}));
  return { response, data };
}
