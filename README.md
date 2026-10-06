# AI Attendance Marker

The backend is a FastAPI application that loads configuration from environment variables, connects to MySQL through SQLAlchemy, and exposes a health check, JWT login, and admin student and teacher rosters. ORM models and Alembic migrations define the attendance schema. Passwords are stored only as Argon2id hashes. Admins and teachers enroll a student's face as an embedding. A recognition service compares a later frame with those embeddings, and staff mark attendance from that match.

## Requirements

- Python 3.12 or newer
- pip
- MySQL 8 with a database named `ai_attendance`

The API process starts even when MySQL is down. `GET /health` is the check that opens a pooled connection and runs a query.

## Project layout

```text
backend/
  app/
    main.py              # FastAPI app factory and router mounting
    config.py            # Environment-based settings, including MySQL credentials
    database/            # Engine, SessionLocal, get_db
    models/              # SQLAlchemy models (users, classes, attendance, audit)
  alembic/               # Migration scripts
  alembic.ini
    schemas/             # Pydantic request and response models
    routers/             # HTTP routes, including registration, login, role gates, students, teachers, and attendance
    services/            # Registration, login, rosters, classes, face enrollment, recognition, and attendance
    auth/                # Argon2id hashing, JWT access tokens, role dependencies
    ai/                  # OpenCV face checks, embeddings, and recognition
    utils/               # Shared helpers (empty until a later phase)
  tests/
  requirements.txt
  .env.example
```

## Start the backend

From the repository root:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and set `MYSQL_PASSWORD`. Create the database once:

```sql
CREATE DATABASE ai_attendance CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Apply the schema:

```bash
alembic upgrade head
```

Then start the API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The site is at [http://127.0.0.1:8000/](http://127.0.0.1:8000/). Sign in and registration are on that same server. After sign-in, the dashboard is at [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard). Interactive API docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Test `/health`

With the server running and MySQL accepting connections:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok","app":"AI Attendance Marker","environment":"development","database":"connected"}
```

If MySQL cannot be reached, the same route returns HTTP 503:

```json
{"status":"unavailable","app":"AI Attendance Marker","environment":"development","database":"disconnected"}
```

## Authentication

`JWT_SECRET` in `backend/.env` must be at least 32 bytes. Tokens expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 60).

Create an account, then sign in. `POST /auth/register` accepts `STUDENT`, `TEACHER`, and `ADMIN`. It stores an Argon2id password hash and returns the public user. A duplicate email returns HTTP 409. An admin can add, update, and deactivate students, teachers, and classes from the dashboard.

```bash
curl -s -X POST http://127.0.0.1:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Ada","last_name":"Lovelace","email":"ada@school.edu","password":"a-real-password","role":"STUDENT"}'
```

Then sign in with the same email and password:

```bash
curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@school.edu","password":"a-real-password"}'
```

A matching active user receives:

```json
{"access_token":"<jwt>","token_type":"bearer","expires_in":3600}
```

Send that token on later requests:

```bash
curl -s http://127.0.0.1:8000/auth/me \
  -H "Authorization: Bearer <jwt>"
```

`GET /auth/me` returns the signed-in user for any role. `GET /admin/me`, `GET /teacher/me`, and `GET /student/me` return 200 only for that role. A missing or invalid token is HTTP 401. A valid token for a different role is HTTP 403. Unknown emails, wrong passwords, and inactive accounts all return the same 401 body from login.

## Student roster

Admins create and maintain students. A student is a `STUDENT` user plus one profile. `student_id` is the profile id. `roll_number` is the school's identifier. `class_id` is optional and must refer to an existing active class; there is no class-management API in this phase. `DELETE /admin/students/{student_id}` deactivates the account and keeps the row, so the student can no longer sign in. Creating or updating a student does not store a face encoding.

Sign in as an admin, then create a student:

```bash
curl -s -X POST http://127.0.0.1:8000/admin/students \
  -H "Authorization: Bearer <admin-jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Ada","last_name":"Lovelace","email":"ada@school.edu","password":"a-real-password","roll_number":"2026-014","gender":"FEMALE","date_of_birth":"2010-12-10"}'
```

A duplicate email or roll number returns HTTP 409. A missing class returns HTTP 404. An inactive class returns HTTP 409. Invalid fields return HTTP 422, and the password is not echoed in that body.

List, search, and page:

```bash
curl -s "http://127.0.0.1:8000/admin/students?search=ada&status=ACTIVE&page=1&page_size=20" \
  -H "Authorization: Bearer <admin-jwt>"
```

`search` matches first name, last name, full name, email, phone, and roll number. `status` is `ACTIVE` or `INACTIVE`. `class_id` and `gender` are optional filters. The list includes deactivated students unless `status` is set.

`GET /admin/students/{student_id}` returns one student. `PATCH /admin/students/{student_id}` updates any of the same fields. Send `class_id: null` to withdraw the current class. A student reads their own roster row with `GET /student/profile`. A student account created only through `POST /auth/register` has no profile yet, so that route returns HTTP 404. A missing or invalid token is HTTP 401. The right token for the wrong role is HTTP 403.

## Teacher profiles

Admins create and maintain teachers. A teacher is a `TEACHER` user plus one profile. `teacher_id` is the profile id. `employee_id` is unique and is stored as `employee_code`. This phase does not assign classes or subjects.

```bash
curl -s -X POST http://127.0.0.1:8000/admin/teachers \
  -H "Authorization: Bearer <admin-jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Grace","last_name":"Hopper","email":"grace@school.edu","password":"a-real-password","employee_id":"T-014","department":"Mathematics"}'
```

A duplicate email or employee id returns HTTP 409. `GET /admin/teachers` searches name, email, phone, employee id, and department, and accepts `status`, `department`, `page`, and `page_size`. `GET /admin/teachers/{teacher_id}` returns one teacher. `PATCH` updates the account and profile. `DELETE` deactivates the account and keeps the row. Class and subject assignment is separate from the teacher profile.

A teacher reads `GET /teacher/profile` and may `PATCH` only `first_name`, `last_name`, `phone`, and `department`. Sending `employee_id`, `email`, `password`, or `status` returns HTTP 403 and does not change the profile. A teacher account created only through `POST /auth/register` has no profile yet, so those routes return HTTP 404.

## Classes and subjects

Admins manage classes and the subjects taught in them. A class is unique for a name, section, and academic year. A subject code is unique inside a class. A class has one class teacher, and a subject has one teacher. A student has one enrollment row per class and at most one active enrollment.

```bash
curl -s -X POST http://127.0.0.1:8000/admin/classes \
  -H "Authorization: Bearer <admin-jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Grade 10","section":"A","academic_year":"2026-2027"}'
```

`GET /admin/classes` and `GET /admin/subjects` search, filter, and page. `DELETE` deactivates the row and keeps it. Assign a class teacher with `PUT /admin/classes/{class_id}/teacher` and a subject teacher with `PUT /admin/subjects/{subject_id}/teacher`. `DELETE` on those teacher paths clears the assignment. Assigning the teacher who is already in that slot does not create a second assignment.

Enroll a student with `POST /admin/classes/{class_id}/students`. A student who is already active in that class, or already active in another class, receives HTTP 409. Move them with `POST /admin/classes/{class_id}/students/{student_id}/move`. Remove them with `DELETE /admin/classes/{class_id}/students/{student_id}`, which withdraws the enrollment and keeps the row.

A teacher lists only assigned active classes and subjects at `GET /teacher/classes` and `GET /teacher/subjects`. A class is assigned when the teacher is its class teacher or teaches an active subject in it.

## Face enrollment

An admin, or a teacher assigned to the student's active class, enrolls a face from one captured image. A teacher is assigned when they are the class teacher or teach an active subject in that class. Students cannot enroll a face. The image must contain exactly one face and pass a basic size, brightness, and blur check. The API stores a 128-number embedding and does not keep the image.

`GET /students/{student_id}/face` returns `NOT_ENROLLED` or `ENROLLED`, plus the active sample count. `POST /students/{student_id}/face` adds a sample. A student can have up to five active samples. `PUT /students/{student_id}/face` replaces every stored sample with the new image.

```bash
curl -s -X POST http://127.0.0.1:8000/students/1/face \
  -H "Authorization: Bearer <admin-or-teacher-jwt>" \
  -F "image=@face.png"
```

A frame with no face, more than one face, or poor quality returns HTTP 422. An inactive student returns HTTP 409. A missing student, or a student outside a teacher's classes, returns HTTP 404. The response does not include the embedding.

`face-recognition` depends on `dlib`, and building `dlib` needs CMake and a C++ compiler. If that library is not installed, these routes return HTTP 503. The automated tests do not need `dlib` or a camera.

## Face recognition

`recognize_frame` in `app.services.face_recognition` takes one image and returns the best enrolled student:

```json
{"student_id": 1, "matched": true, "confidence": 0.75, "distance": 0.25}
```

`student_id` is set only when `matched` is true. `distance` is the Euclidean distance to the closest active sample of the closest student. `confidence` is `1 - distance`, clamped to 0 through 1. A face matches when `distance` is less than or equal to `FACE_MATCH_THRESHOLD` (default `0.6`). A farther face is rejected: `matched` is false and `student_id` is null. The same four fields are returned when the frame has no face, with `distance` null and `confidence` 0.

A frame may contain several faces. Each face is encoded and compared on its own. The return value is the closest accepted face. The other faces are on `faces` and are not dropped. This function does not write an attendance row.

Active embeddings are loaded with one query and kept in memory for `FACE_EMBEDDING_CACHE_SECONDS` (default 60). Later frames reuse that copy. Saving or replacing a face sample clears the cache. Inactive samples and inactive student accounts are left out of the gallery.

## Attendance

`POST /attendance/mark` takes a camera frame plus `class_id` and `subject_id`. The frame is recognized, the student must be active and enrolled in that class, and the subject must belong to the class. The stored row uses the subject teacher, or the class teacher when the subject has none.

```bash
curl -s -X POST http://127.0.0.1:8000/attendance/mark \
  -H "Authorization: Bearer <admin-or-teacher-jwt>" \
  -F "class_id=1" \
  -F "subject_id=1" \
  -F "image=@frame.png"
```

A new row is HTTP 201. The same student, subject, and date again is HTTP 200 and does not insert a second row. The database unique key `uq_attendance_student_subject_date` is the same guard when two requests arrive together.

```json
{"attendance_id": 1, "student_id": 4, "class_id": 1, "subject_id": 1, "teacher_id": 2, "date": "2026-10-06", "check_in_time": "2026-10-06T04:15:00Z", "status": "PRESENT", "confidence_score": 0.75, "recognition_method": "FACE_RECOGNITION", "created": true}
```

`check_in_time` is UTC. `date` is the calendar date in `ATTENDANCE_TIMEZONE` (default `UTC`). A face check-in at or after `ATTENDANCE_LATE_AFTER` (`HH:MM` in that timezone) is `LATE`. Leave the cutoff empty to record `PRESENT`.

`POST /attendance/manual` records `PRESENT`, `ABSENT`, `LATE`, or `EXCUSED` for a known student without a frame. `recognition_method` is `MANUAL` and `confidence_score` is null. It follows the same duplicate rule and does not change a row that is already stored.

`GET /attendance/today` lists today's rows. `GET /classes/{class_id}/attendance` lists one class, with an optional `date` and `subject_id`. `GET /students/{student_id}/attendance` is that student's history. A student reads their own history at `GET /student/attendance`. A teacher sees a class only when they are its class teacher or teach the subject. A frame that matches nobody returns HTTP 422 and stores nothing. An inactive student, or a student who is not in the class, returns HTTP 409.

Run the automated tests from `backend/` with the virtual environment active:

```bash
pytest
```

The live connection test skips when MySQL is not reachable. The other tests use stand-ins or SQLite and do not need MySQL.
