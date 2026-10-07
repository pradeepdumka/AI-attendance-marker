FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# OpenCV and the dlib wheel need these at runtime. tzdata supplies IANA
# names such as Asia/Kolkata for attendance dates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt

# The PyPI package named dlib is source-only on Linux. dlib-bin is the same
# library as a wheel. face-recognition depends on the source package, so it
# is installed without dependencies and the wheel satisfies `import dlib`.
RUN pip install --upgrade pip \
    && pip install "dlib-bin==20.0.1.post1" \
    && pip install --no-deps "face-recognition==1.3.0" \
    && pip install \
        "face-recognition-models>=0.3.0" \
        "Pillow>=10" \
    && grep -vi '^face-recognition' /tmp/requirements.txt > /tmp/requirements.docker.txt \
    && pip install -r /tmp/requirements.docker.txt \
    && pip install "setuptools>=70,<81" \
    && python -c "import builtins; builtins.quit = builtins.exit = lambda *a, **k: (_ for _ in ()).throw(SystemExit(1)); import face_recognition, cv2, dlib, fastapi; print('imports ok', cv2.__version__)"

COPY backend /app/backend
COPY frontend /app/frontend
COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
    && chown -R app:app /app

USER app
WORKDIR /app/backend
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
