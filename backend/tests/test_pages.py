"""The public HTML pages are served beside the API."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_home_login_and_register_pages_are_html() -> None:
    with TestClient(create_app()) as client:
        home = client.get("/")
        login = client.get("/login")
        register = client.get("/register")
        dashboard = client.get("/dashboard")
        teachers = client.get("/dashboard/teachers")
        student_form = client.get("/dashboard/students/new")
        styles = client.get("/assets/styles.css")
        script = client.get("/assets/app.js")

    assert home.status_code == login.status_code == register.status_code == 200
    assert "text/html" in home.headers["content-type"]
    assert "site-header" in home.text
    assert "site-footer" in home.text
    assert 'id="login-form"' in login.text
    assert 'id="register-form"' in register.text
    assert dashboard.status_code == teachers.status_code == student_form.status_code == 200
    assert "Teacher management" in dashboard.text
    assert "Add new student" in dashboard.text
    assert "Add new class" in dashboard.text
    assert teachers.text == dashboard.text == student_form.text
    assert 'id="nav-toggle"' in dashboard.text
    assert styles.status_code == script.status_code == 200
    assert "--ink:" in styles.text
