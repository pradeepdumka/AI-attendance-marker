"""The public HTML pages are served beside the API."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_home_and_login_pages_are_html() -> None:
    with TestClient(create_app()) as client:
        home = client.get("/")
        login = client.get("/login")
        register = client.get("/register", follow_redirects=False)
        dashboard = client.get("/dashboard")
        teachers = client.get("/dashboard/teachers")
        student_form = client.get("/dashboard/students/new")
        subjects = client.get("/dashboard/subjects")
        faces = client.get("/dashboard/faces")
        attendance = client.get("/dashboard/attendance")
        mark = client.get("/dashboard/attendance/mark")
        styles = client.get("/assets/styles.css")
        script = client.get("/assets/app.js")
        components = client.get("/assets/components.js")
        dashboard_script = client.get("/assets/dashboard.js")

    assert home.status_code == login.status_code == 200
    assert register.status_code == 307
    assert register.headers["location"] == "/login"
    assert "text/html" in home.headers["content-type"]
    assert "site-header" in home.text
    assert "site-footer" in home.text
    assert 'id="login-form"' in login.text
    assert 'href="/register"' not in home.text
    assert 'href="/register"' not in login.text
    assert "Create an account" not in home.text
    assert "Create an account" not in login.text
    assert dashboard.status_code == teachers.status_code == student_form.status_code == 200
    assert "Teacher management" in dashboard.text
    assert "Add new student" in dashboard.text
    assert "Add new class" in dashboard.text
    assert "Subject management" in dashboard.text
    assert "Face enrollment" in dashboard.text
    assert "Mark attendance" in dashboard.text
    assert "My Classes" in dashboard.text
    assert "Start attendance" in dashboard.text
    assert "My Profile" in dashboard.text
    assert "My Attendance" in dashboard.text
    assert "Attendance %" in dashboard.text
    assert 'data-roles="STUDENT"' in dashboard.text
    assert 'data-roles="TEACHER"' in dashboard.text
    assert 'data-roles="ADMIN"' in dashboard.text
    assert 'src="/assets/components.js"' in dashboard.text
    assert teachers.text == dashboard.text == student_form.text == subjects.text == faces.text == attendance.text == mark.text
    assert 'id="nav-toggle"' in dashboard.text
    assert styles.status_code == script.status_code == components.status_code == dashboard_script.status_code == 200
    assert "const Dashboard" in components.text
    assert "function teacherAllowed" in dashboard_script.text
    assert "function studentAllowed" in dashboard_script.text
    assert "--ink:" in styles.text
