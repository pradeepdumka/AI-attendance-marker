/* Reusable pieces for the admin, teacher, and student dashboards. */
const Dashboard = (() => {
  const statusNames = {
    ACTIVE: ["Active", "pill-present"],
    INACTIVE: ["Inactive", "pill-absent"],
    PRESENT: ["Present", "pill-present"],
    ABSENT: ["Absent", "pill-absent"],
    LATE: ["Late", "pill-late"],
    EXCUSED: ["Excused", "pill-late"],
    ENROLLED: ["Enrolled", "pill-present"],
    NOT_ENROLLED: ["Not enrolled", "pill-absent"],
    WITHDRAWN: ["Withdrawn", "pill-absent"],
    OPEN: ["Open", "pill-present"],
    CLOSED: ["Closed", "pill-absent"],
  };

  function messageRow(span, text) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = span;
    cell.className = "empty";
    cell.textContent = text;
    row.append(cell);
    return row;
  }

  function statusPill(status) {
    const [text, tone] = statusNames[status] || [label(status) || "—", "pill-absent"];
    const pill = document.createElement("span");
    pill.className = `pill ${tone}`;
    pill.textContent = text;
    return pill;
  }

  function textCell(text) {
    const cell = document.createElement("td");
    cell.textContent = text ? String(text) : "—";
    return cell;
  }

  function renderRows(body, columns, items, emptyText) {
    if (!items.length) {
      body.replaceChildren(messageRow(columns.length, emptyText));
      return;
    }
    body.replaceChildren(...items.map((item) => {
      const row = document.createElement("tr");
      row.append(...columns.map((column) => column.cell(item)));
      return row;
    }));
  }

  function summary(page, pageSize, total) {
    const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, total);
    return `Showing ${start} – ${end} of ${total}`;
  }

  function setPager(previous, next, page, pageSize, total) {
    previous.disabled = page <= 1;
    next.disabled = page * pageSize >= total;
  }

  function statCard(title, value, href) {
    const item = document.createElement("li");
    const heading = document.createElement("h3");
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = title;
      heading.append(link);
    } else {
      heading.textContent = title;
    }
    const count = document.createElement("p");
    count.className = "stat";
    count.textContent = value === null || value === undefined ? "—" : String(value);
    item.append(heading, count);
    return item;
  }

  function fillSelect(select, choices, current, blank) {
    select.replaceChildren();
    if (blank != null) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = blank;
      select.append(option);
    }
    for (const [optionValue, optionLabel] of choices) {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionLabel;
      select.append(option);
    }
    const wanted = current == null ? "" : String(current);
    select.value = [...select.options].some((option) => option.value === wanted) ? wanted : "";
  }

  function label(role) {
    const text = String(role || "").toLowerCase().replaceAll("_", " ");
    return text ? text[0].toUpperCase() + text.slice(1) : "";
  }

  return {
    messageRow,
    statusPill,
    textCell,
    renderRows,
    summary,
    setPager,
    statCard,
    fillSelect,
    label,
  };
})();
